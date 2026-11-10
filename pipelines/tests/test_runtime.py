"""Runtime writes: per-tenant replace and insert-only append."""

import pytest

pytest.importorskip("pyspark", reason="PySpark runs in the Spark container or CI")

from pyspark.sql import SparkSession

from txlens_pipelines.runtime import LocalRuntime, tenant_predicate

pytestmark = pytest.mark.spark
SCHEMA = "tenant_id string, k int, v string"


def values(runtime: LocalRuntime) -> list[tuple[str, int, str]]:
    rows = runtime.read("silver", "t").orderBy("tenant_id", "k").collect()
    return [(r.tenant_id, r.k, r.v) for r in rows]


def test_replace_tenants_leaves_other_tenants_alone(
    spark: SparkSession, runtime: LocalRuntime
) -> None:
    first = spark.createDataFrame([("center_a", 1, "a1"), ("center_b", 1, "b1")], SCHEMA)
    runtime.replace_tenants(first, "silver", "t", ["center_a", "center_b"])
    again = spark.createDataFrame([("center_a", 2, "a2")], SCHEMA)
    runtime.replace_tenants(again, "silver", "t", ["center_a"])
    assert values(runtime) == [("center_a", 2, "a2"), ("center_b", 1, "b1")]


def test_append_new_is_insert_only_and_idempotent(
    spark: SparkSession, runtime: LocalRuntime
) -> None:
    rows = spark.createDataFrame([("center_a", 1, "old"), ("center_a", 1, "dup")], SCHEMA)
    runtime.append_new(rows, "silver", "t", ["tenant_id", "k"])
    changed = spark.createDataFrame([("center_a", 1, "new"), ("center_a", 2, "x")], SCHEMA)
    runtime.append_new(changed, "silver", "t", ["tenant_id", "k"])
    runtime.append_new(changed, "silver", "t", ["tenant_id", "k"])
    found = values(runtime)
    assert len(found) == 2
    assert found[1] == ("center_a", 2, "x")
    assert found[0][2] in {"old", "dup"}  # never updated to "new"


def test_tenant_predicate_rejects_unsafe_ids() -> None:
    assert tenant_predicate(["center_a"]) == "tenant_id IN ('center_a')"
    with pytest.raises(ValueError):
        tenant_predicate(["x') OR 1=1 --"])
