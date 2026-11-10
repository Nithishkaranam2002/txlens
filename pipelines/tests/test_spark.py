from pathlib import Path

import pytest

pytest.importorskip("pyspark", reason="PySpark runs in the Spark container or CI")

from txlens_pipelines.spark import build_spark_session

pytestmark = pytest.mark.spark


def test_delta_round_trip(tmp_path: Path) -> None:
    spark = build_spark_session("txlens-test")
    path = str(tmp_path / "delta_table")
    spark.createDataFrame([(1, "kidney"), (2, "heart")], ["id", "organ"]).write.format(
        "delta"
    ).save(path)
    rows = spark.read.format("delta").load(path).orderBy("id").collect()
    assert [r.organ for r in rows] == ["kidney", "heart"]
