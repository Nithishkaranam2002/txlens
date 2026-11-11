"""`python -m txlens_pipelines`: run the pipeline locally (`make pipeline`)."""

import argparse

from txlens_pipelines.config import PipelineConfig, deid_key_from_env
from txlens_pipelines.pipeline import LAYER_FUNCTIONS, run
from txlens_pipelines.runtime import LocalRuntime


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description="Run the TxLens medallion pipeline locally.")
    parser.add_argument(
        "--layers",
        default=",".join(LAYER_FUNCTIONS),
        help=f"comma-separated subset of {','.join(LAYER_FUNCTIONS)} (default: all)",
    )
    parser.add_argument("--tenant", action="append", help="limit to a tenant (repeatable)")
    parser.add_argument("--raw-root", default="data/raw")
    parser.add_argument("--vocab-root", default="omop/vocab_subset")
    parser.add_argument("--lake-root", default="lake")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Build a local runtime and run the requested layers."""
    from txlens_pipelines.spark import build_spark_session

    args = parse_args(argv)
    config = PipelineConfig(args.raw_root, args.vocab_root, deid_key=deid_key_from_env()).only(
        args.tenant
    )
    spark = build_spark_session("txlens-pipeline")
    try:
        runtime = LocalRuntime(spark, args.raw_root, args.vocab_root, args.lake_root)
        run(runtime, config, [layer.strip() for layer in args.layers.split(",")])
    finally:
        spark.stop()
    return 0
