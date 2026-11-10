"""Run the medallion layers in order and print what each one did."""

from collections.abc import Callable

from txlens_pipelines.bronze.run import run_bronze
from txlens_pipelines.config import PipelineConfig
from txlens_pipelines.gold.run import run_gold
from txlens_pipelines.runtime import Runtime
from txlens_pipelines.silver.run import run_silver

Layer = Callable[[Runtime, PipelineConfig], dict[str, int]]


def run_load(runtime: Runtime, config: PipelineConfig) -> dict[str, int]:
    """Postgres load; imported here because psycopg exists only in local runs."""
    from txlens_pipelines.load.run import run_load as load

    return load(runtime, config)


LAYER_FUNCTIONS: dict[str, Layer] = {
    "bronze": run_bronze,
    "silver": run_silver,
    "gold": run_gold,
    "load": run_load,
}


def run(runtime: Runtime, config: PipelineConfig, layers: list[str]) -> dict[str, dict[str, int]]:
    """Run `layers` (in medallion order) and return each layer's row counts."""
    unknown = set(layers) - LAYER_FUNCTIONS.keys()
    if unknown:
        raise ValueError(f"unknown layers: {sorted(unknown)}; known: {list(LAYER_FUNCTIONS)}")
    results = {}
    for layer in (name for name in LAYER_FUNCTIONS if name in layers):
        results[layer] = LAYER_FUNCTIONS[layer](runtime, config)
        print(f"[{layer}] " + ", ".join(f"{k}={v:,}" for k, v in results[layer].items()))
    return results
