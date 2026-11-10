"""Ingest every raw source into bronze. Already-ingested file versions are skipped."""

from pyspark.sql import DataFrame

from txlens_pipelines.bronze import fhir, labs, notes, pdfs
from txlens_pipelines.bronze.files import FILE_KEYS, only_new, read_files
from txlens_pipelines.config import PipelineConfig
from txlens_pipelines.runtime import Runtime


def source_patterns(config: PipelineConfig) -> dict[str, list[str]]:
    """Raw file patterns per bronze table, for the configured tenants."""
    tenants = [t.tenant_id for t in config.tenants]
    return {
        labs.TABLE: [f"labs/{t.lab_file}" for t in config.tenants],
        fhir.TABLE: [f"fhir/{t}/*.json" for t in tenants],
        notes.TABLE: [f"notes/{t}/*.txt" for t in tenants],
        pdfs.TABLE: [f"pathology/{t}/*.pdf" for t in tenants],
    }


def to_bronze(table: str, files: DataFrame, config: PipelineConfig) -> DataFrame:
    """The per-source transform."""
    if table == labs.TABLE:
        return labs.ingest(files, config)
    return {fhir.TABLE: fhir.ingest, notes.TABLE: notes.ingest, pdfs.TABLE: pdfs.ingest}[table](
        files
    )


def run_bronze(runtime: Runtime, config: PipelineConfig) -> dict[str, int]:
    """Append new file versions to each bronze table; returns rows added per table."""
    added = {}
    for table, patterns in source_patterns(config).items():
        files = only_new(runtime, read_files(runtime, patterns, config.batch_id), table)
        rows = to_bronze(table, files, config)
        added[table] = rows.count()
        if added[table]:
            runtime.append_new(rows, "bronze", table, FILE_KEYS)
    return added
