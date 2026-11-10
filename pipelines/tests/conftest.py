"""Shared Spark fixtures. Spark tests skip on hosts without PySpark (it runs in the Spark
container and in CI)."""

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

    from txlens_pipelines.runtime import LocalRuntime

OMOP_FAKE = Path(__file__).parents[2] / "omop" / "tests" / "fixtures" / "athena_fake"


@pytest.fixture(scope="session")
def spark() -> Iterator["SparkSession"]:
    """One local Spark session with Delta for the whole test run."""
    pytest.importorskip("pyspark", reason="PySpark runs in the Spark container or CI")
    from txlens_pipelines.spark import build_spark_session

    session = build_spark_session("txlens-tests")
    yield session
    session.stop()


@pytest.fixture
def runtime(spark: "SparkSession", tmp_path: Path) -> "LocalRuntime":
    """A runtime with an empty lake in a temp folder and the fake vocabulary plus our mappings."""
    import shutil

    from txlens_omop.mappings import LOCAL_CONCEPT_FILE, STCM_FILE, mappings_dir
    from txlens_pipelines.runtime import LocalRuntime

    vocab = tmp_path / "vocab"
    shutil.copytree(OMOP_FAKE, vocab)
    for name in (LOCAL_CONCEPT_FILE, STCM_FILE):
        shutil.copyfile(mappings_dir() / name, vocab / name)
    return LocalRuntime(spark, str(tmp_path / "raw"), str(vocab), str(tmp_path / "lake"))
