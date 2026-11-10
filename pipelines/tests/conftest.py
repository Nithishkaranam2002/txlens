"""Shared Spark fixtures. Spark tests skip on hosts without PySpark (it runs in the Spark
container and in CI)."""

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

    from txlens_pipelines.runtime import LocalRuntime


# Test data is tiny, so per-job overhead dominates: one partition, no whole-stage code
# generation (compiling each plan costs more than running it). This halves the Spark test time.
TEST_SPARK_CONF = {
    "spark.sql.shuffle.partitions": "1",
    "spark.default.parallelism": "1",
    "spark.databricks.delta.snapshotPartitions": "1",
    "spark.sql.codegen.wholeStage": "false",
}


@pytest.fixture(scope="session")
def spark() -> Iterator["SparkSession"]:
    """One local Spark session with Delta for the whole test run."""
    pytest.importorskip("pyspark", reason="PySpark runs in the Spark container or CI")
    from txlens_pipelines.spark import build_spark_session

    session = build_spark_session("txlens-tests", TEST_SPARK_CONF)
    yield session
    session.stop()


@pytest.fixture
def runtime(spark: "SparkSession", tmp_path: Path) -> "LocalRuntime":
    """A runtime with an empty lake in a temp folder and the small test vocabulary."""
    from testvocab import write_vocab

    from txlens_pipelines.runtime import LocalRuntime

    vocab = write_vocab(tmp_path / "vocab")
    return LocalRuntime(spark, str(tmp_path / "raw"), str(vocab), str(tmp_path / "lake"))


@pytest.fixture(scope="session")
def built(spark: "SparkSession", tmp_path_factory: pytest.TempPathFactory) -> "LocalRuntime":
    """Bronze, silver and gold built once from the hand-written raw rows (see rawfiles.py).

    Shared by the silver and gold tests, which only read it. The quarantine limit is off:
    tiny data."""
    from rawfiles import write_raw
    from testvocab import write_vocab

    from txlens_pipelines.config import PipelineConfig
    from txlens_pipelines.pipeline import run
    from txlens_pipelines.runtime import LocalRuntime

    root = tmp_path_factory.mktemp("built")
    runtime = LocalRuntime(
        spark, str(write_raw(root / "raw")), str(write_vocab(root / "vocab")), str(root / "lake")
    )
    config = PipelineConfig(runtime.raw_root, runtime.vocab_root, max_quarantine_rate=1.0)
    run(runtime, config, ["bronze", "silver", "gold"])
    return runtime
