"""Stable ids: unchanged by reordering and by new data; separate per tenant and table."""

import pytest

pytest.importorskip("pyspark", reason="PySpark runs in the Spark container or CI")

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from txlens_pipelines import ids
from txlens_pipelines.config import PipelineConfig
from txlens_pipelines.ids import IdRangeExhaustedError, assign_ids, natural_key
from txlens_pipelines.runtime import LocalRuntime

pytestmark = pytest.mark.spark
CONFIG = PipelineConfig("raw", "vocab")


def people(spark: SparkSession, rows: list[tuple[str, str]]) -> DataFrame:
    df = spark.createDataFrame(rows, "tenant_id string, mrn string")
    return df.withColumn("natural_key", natural_key("tenant_id", "mrn"))


def id_of(runtime: LocalRuntime, df: DataFrame, table: str = "person") -> dict[str, int]:
    out = assign_ids(runtime, df, table, "person_id", CONFIG)
    return {r.mrn: r.person_id for r in out.collect()}


def test_ids_start_in_the_tenant_range(spark: SparkSession, runtime: LocalRuntime) -> None:
    found = id_of(runtime, people(spark, [("center_a", "A1"), ("center_b", "B1")]))
    assert found == {"A1": 100_000_001, "B1": 200_000_001}


def test_ids_survive_reordering_and_new_data(spark: SparkSession, runtime: LocalRuntime) -> None:
    first = id_of(runtime, people(spark, [("center_a", "A2"), ("center_a", "A1")]))
    shuffled = people(spark, [("center_a", "A3"), ("center_a", "A1"), ("center_a", "A0")])
    second = id_of(runtime, shuffled.orderBy(F.rand(seed=7)))
    assert second["A1"] == first["A1"]
    assert {second["A3"], second["A0"]} == {100_000_003, 100_000_004}
    again = id_of(runtime, shuffled)
    assert again == second  # re-running assigns nothing new


def test_tables_have_independent_ids(spark: SparkSession, runtime: LocalRuntime) -> None:
    df = people(spark, [("center_a", "A1")])
    assert id_of(runtime, df, "person") == id_of(runtime, df, "death") == {"A1": 100_000_001}


def test_full_range_fails(
    spark: SparkSession, runtime: LocalRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ids, "ID_RANGE", 2)
    with pytest.raises(IdRangeExhaustedError):
        id_of(runtime, people(spark, [("center_a", "A1"), ("center_a", "A2")]))
