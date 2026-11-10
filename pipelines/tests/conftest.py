"""Shared Spark fixtures. Spark tests skip on hosts without PySpark (it runs in the Spark
container and in CI)."""

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

    from txlens_pipelines.runtime import LocalRuntime


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
    """A runtime with an empty lake in a temp folder and the small test vocabulary."""
    from testvocab import write_vocab

    from txlens_pipelines.runtime import LocalRuntime

    vocab = write_vocab(tmp_path / "vocab")
    return LocalRuntime(spark, str(tmp_path / "raw"), str(vocab), str(tmp_path / "lake"))
