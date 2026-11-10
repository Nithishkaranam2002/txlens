"""Run the medallion layers in order and print what each one did."""

from collections.abc import Callable

from txlens_pipelines.bronze.run import run_bronze
from txlens_pipelines.config import PipelineConfig
from txlens_pipelines.runtime import Runtime

Layer = Callable[[Runtime, PipelineConfig], dict[str, int]]
LAYER_FUNCTIONS: dict[str, Layer] = {"bronze": run_bronze}


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
