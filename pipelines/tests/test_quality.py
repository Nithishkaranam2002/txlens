"""Quarantine rules and table contracts."""

import pytest

pytest.importorskip("pyspark", reason="PySpark runs in the Spark container or CI")

import pandera.pyspark as pa
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import IntegerType, StringType

from txlens_pipelines.quality import (
    ContractViolationError,
    Rule,
    apply_rules,
    check_primary_key,
    check_quarantine_rate,
    to_quarantine,
    validate_schema,
)

pytestmark = pytest.mark.spark
SCHEMA = "tenant_id string, bronze_source_file string, bronze_source_row long, v string"


def test_first_failing_rule_names_the_reason(spark: SparkSession) -> None:
    df = spark.createDataFrame(
        [("center_a", "f", 1, "ok"), ("center_a", "f", 2, None), ("center_a", "f", 3, "x?")], SCHEMA
    )
    rules = [
        Rule("missing_value", F.col("v").isNull(), F.lit("empty")),
        Rule("unparseable_value", F.col("v").contains("?"), F.col("v")),
        Rule("bad_date", F.col("v").isNull() | F.col("v").contains("?"), F.lit("never first")),
    ]
    good, bad = apply_rules(df, rules)
    assert [r.v for r in good.collect()] == ["ok"]
    reasons = {r.bronze_source_row: (r.reason_code, r.detail) for r in bad.collect()}
    assert reasons == {2: ("missing_value", "empty"), 3: ("unparseable_value", "x?")}
    quarantined = to_quarantine(bad, "labs", "batch1").orderBy("source_row").first()
    assert quarantined is not None
    assert (quarantined.entity, quarantined.source_row, quarantined.batch_id) == (
        "labs",
        2,
        "batch1",
    )
    assert '"bronze_source_row":2' in quarantined.row_json


@pytest.mark.usefixtures("spark")  # F.lit needs an active Spark session
def test_unknown_reason_is_rejected() -> None:
    with pytest.raises(ValueError):
        Rule("looks_odd", F.lit(True), F.lit(""))


def test_quarantine_rate_limit() -> None:
    check_quarantine_rate("labs", good=95, bad=5, max_rate=0.05)
    with pytest.raises(ContractViolationError, match=r"6\.0%"):
        check_quarantine_rate("labs", good=94, bad=6, max_rate=0.05)


def test_primary_key_must_be_unique_and_not_null(spark: SparkSession) -> None:
    check_primary_key(spark.createDataFrame([(1,), (2,)], "id int"), "t", ["id"])
    with pytest.raises(ContractViolationError, match="duplicate"):
        check_primary_key(spark.createDataFrame([(1,), (1,)], "id int"), "t", ["id"])
    with pytest.raises(ContractViolationError, match="null"):
        check_primary_key(spark.createDataFrame([(1,), (None,)], "id int"), "t", ["id"])


class Example(pa.DataFrameModel):  # type: ignore[type-arg]
    id: IntegerType = pa.Field(nullable=False)
    name: StringType = pa.Field()


def test_schema_contract_fails_on_wrong_types(spark: SparkSession) -> None:
    validate_schema(spark.createDataFrame([(1, "a")], "id int, name string"), Example, "t")
    with pytest.raises(ContractViolationError, match="pandera"):
        validate_schema(spark.createDataFrame([("1", "a")], "id string, name string"), Example, "t")
