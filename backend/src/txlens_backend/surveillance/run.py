"""One surveillance run for one center: load series, evaluate, store flags, record the run.

Everything happens in one tenant transaction, so a run is all-or-nothing, and running the
same center and date again changes nothing (flags are keyed by their evidence).
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from sqlalchemy import Connection, Engine, text

from txlens_backend import audit
from txlens_backend.db import tenant_transaction
from txlens_backend.surveillance import repository
from txlens_backend.surveillance.config import SurveillanceConfig, load_configs
from txlens_backend.surveillance.engine import evaluate
from txlens_backend.surveillance.model import Flag


@dataclass(frozen=True)
class RunSummary:
    """What one run did in one center."""

    run_id: str
    tenant_id: str
    as_of: date
    patients: int
    flags_opened: int
    flags_cleared: int


def transplant_configs() -> list[SurveillanceConfig]:
    """The built-in configs whose patients live in the OMOP tables."""
    return [c for c in load_configs().values() if c.anchor == repository.TRANSPLANT_ANCHOR]


def list_tenants(engine: Engine) -> list[str]:
    """Every center (the tenant registry is readable without a tenant)."""
    with engine.connect() as conn:
        return list(conn.execute(text("SELECT tenant_id FROM app.tenant ORDER BY 1")).scalars())


def run_tenant(
    engine: Engine, tenant_id: str, as_of: date, configs: Iterable[SurveillanceConfig]
) -> RunSummary:
    """Evaluate every patient of one center as of `as_of` and store the flags (commits)."""
    with tenant_transaction(engine, tenant_id) as conn:
        return run_in(conn, tenant_id, as_of, configs)


def run_in(
    conn: Connection, tenant_id: str, as_of: date, configs: Iterable[SurveillanceConfig]
) -> RunSummary:
    """The run itself, inside an open transaction already scoped to `tenant_id`."""
    configs = list(configs)
    run_id = str(uuid.uuid4())
    flags: dict[int, list[Flag]] = {}
    for config in configs:
        for person_id, series in repository.load_series(conn, config, as_of).items():
            flags[person_id] = evaluate(config, series, as_of)
    opened, cleared = repository.save_flags(conn, run_id, as_of, configs, flags)
    repository.record_run(conn, run_id, as_of, configs, len(flags), opened, cleared)
    audit.record(
        conn, audit.RequestInfo(request_id=run_id, ip=""), None, "surveillance_run",
        "surveillance_run", run_id,
        {"as_of": as_of.isoformat(), "patients": len(flags), "flags_opened": opened,
         "flags_cleared": cleared, "configs": {c.id: c.version for c in configs}},
    )  # fmt: skip
    return RunSummary(run_id, tenant_id, as_of, len(flags), opened, cleared)
