"""End to end on a small generated dataset: every reconciliation check passes, re-runs change
nothing, and ids survive new data arriving."""

from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("pyspark", reason="PySpark runs in the Spark container or CI")

from pyspark.sql import SparkSession
from testvocab import write_vocab

from txlens_datagen.config import GeneratorConfig
from txlens_datagen.generate import generate
from txlens_evals.pipeline_reconciliation import Lake, build_report
from txlens_pipelines.config import PipelineConfig
from txlens_pipelines.pipeline import run
from txlens_pipelines.runtime import LocalRuntime

pytestmark = pytest.mark.spark
LAYERS = ["bronze", "silver", "gold"]


def build(spark: SparkSession, root: Path, patients: int) -> tuple[LocalRuntime, Lake]:
    """Generate `patients` into root/data (same seed) and run the pipeline into root/lake."""
    generate(replace(GeneratorConfig(), n_patients=patients), root / "data")
    runtime = LocalRuntime(
        spark, str(root / "data" / "raw"), str(root / "vocab"), str(root / "lake")
    )
    # Small samples can exceed 5% bad rows by chance; the limit is tested elsewhere.
    config = PipelineConfig(runtime.raw_root, runtime.vocab_root, max_quarantine_rate=0.25)
    run(runtime, config, LAYERS)
    return runtime, Lake(spark, runtime.lake_root, None)


def ids(runtime: LocalRuntime) -> dict[tuple[str, str], int]:
    rows = runtime.read("gold", "id_map").collect()
    return {(r.omop_table, r.natural_key): r.omop_id for r in rows}


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("e2e")
    write_vocab(path / "vocab")
    return path


def test_generated_data_reconciles_and_reruns_are_idempotent(
    spark: SparkSession, root: Path
) -> None:
    runtime, lake = build(spark, root, 12)
    report, checks = build_report(
        spark, lake, str(root / "data" / "ground_truth"), runtime.vocab_root, postgres=False
    )
    failed = [c for c in checks if not c.passed]
    assert not failed, report
    before = ids(runtime)
    counts = {t: runtime.read("gold", t).count() for t in ("person", "measurement", "note")}
    config = PipelineConfig(runtime.raw_root, runtime.vocab_root, max_quarantine_rate=0.25)
    run(runtime, config, LAYERS)
    assert ids(runtime) == before
    assert {t: runtime.read("gold", t).count() for t in counts} == counts


def test_ids_survive_new_patients_and_longer_files(spark: SparkSession, root: Path) -> None:
    runtime, _ = build(spark, root, 12)
    before = ids(runtime)
    runtime, lake = build(spark, root, 16)  # same seed: 4 more patients, lab files grow
    after = ids(runtime)
    assert len(after) > len(before)
    assert {k: after[k] for k in before} == before  # every old key kept its id
    _, checks = build_report(
        spark, lake, str(root / "data" / "ground_truth"), runtime.vocab_root, postgres=False
    )
    assert all(c.passed for c in checks)
