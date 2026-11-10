"""Entry point for `make pipeline`. Bronze/silver/gold jobs are built in Step 1.4."""

from txlens_pipelines.spark import build_spark_session


def main() -> None:
    """Start Spark with Delta and report that the runtime works."""
    spark = build_spark_session("txlens-pipeline")
    print(f"Spark {spark.version} with Delta Lake is ready. Pipelines arrive in Step 1.4.")
    spark.stop()


if __name__ == "__main__":
    main()
