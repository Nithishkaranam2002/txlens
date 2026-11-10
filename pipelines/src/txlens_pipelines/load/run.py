"""`load` layer: gold Delta tables -> Postgres cdm, one transaction per tenant."""

from collections.abc import Iterator

import psycopg
from pyspark.sql import DataFrame

from txlens_pipelines.config import PipelineConfig
from txlens_pipelines.load.postgres import LOAD_ORDER, TableRows, database_url, load_tenant
from txlens_pipelines.runtime import Runtime


def stream(df: DataFrame) -> Iterator[tuple[object, ...]]:
    """Rows as tuples, fetched partition by partition (never the whole table in memory)."""
    for row in df.toLocalIterator():
        yield tuple(row)


def run_load(runtime: Runtime, config: PipelineConfig) -> dict[str, int]:
    """Load every configured tenant; returns rows inserted per table (all tenants)."""
    totals: dict[str, int] = {}
    # autocommit: each tenant's `conn.transaction()` is then a real transaction.
    with psycopg.connect(database_url(), autocommit=True) as conn:
        for tenant in config.tenants:
            tables = {}
            for table in LOAD_ORDER:
                df = runtime.read_tenants("gold", table, [tenant.tenant_id])
                tables[table] = TableRows(df.columns, stream(df))
            for table, count in load_tenant(
                conn, tenant.tenant_id, tables, config.batch_id
            ).items():
                totals[table] = totals.get(table, 0) + count
    return totals
