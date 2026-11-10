"""Create a local SparkSession with Delta Lake enabled."""

from delta import configure_spark_with_delta_pip
from pyspark.sql import SparkSession


def build_spark_session(app_name: str = "txlens") -> SparkSession:
    """Return a local SparkSession that can read and write Delta tables."""
    builder = (
        SparkSession.builder.appName(app_name)
        .master("local[*]")
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config(
            "spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog",
        )
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.ui.enabled", "false")
    )
    session: SparkSession = configure_spark_with_delta_pip(builder).getOrCreate()
    return session
