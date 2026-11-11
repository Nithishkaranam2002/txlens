"""Database side of surveillance: read patient series from `cdm`, store and update flags.

Every function runs inside a tenant transaction, so RLS limits it to one center. Only
transplant programs (anchor = transplant date) are loaded from the OMOP tables.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import Connection, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.expression import bindparam

from txlens_backend.services.facts import FACTS_CTE, FACTS_PARAMS, concept_keys
from txlens_backend.surveillance.config import SurveillanceConfig
from txlens_backend.surveillance.model import Flag, Observation, PatientSeries

TRANSPLANT_ANCHOR = "transplant_date"

PATIENTS = text(
    f"WITH {FACTS_CTE} "
    "SELECT f.person_id, f.transplant_date, f.death_date, g.failure_date "
    "FROM facts f LEFT JOIN graft_failure g ON g.person_id = f.person_id "
    "WHERE f.organ = :program AND f.transplant_date IS NOT NULL"
)
MEASUREMENTS = text(
    "SELECT person_id, measurement_date, value_as_number, measurement_concept_id, "
    "measurement_source_concept_id FROM cdm.measurement "
    "WHERE person_id = ANY(:persons) AND measurement_date <= :as_of "
    "AND value_as_number IS NOT NULL "
    "AND (measurement_concept_id = ANY(:standard) OR measurement_source_concept_id = ANY(:source)) "
    "ORDER BY person_id, measurement_date, measurement_datetime, measurement_id"
)
UPSERT = text(
    "INSERT INTO app.surveillance_flag (person_id, run_id, config_id, config_version, rule_id, "
    "rule_group, severity, reason, evidence, evidence_key, as_of) "
    "SELECT r.person_id, :run_id, r.config_id, r.config_version, r.rule_id, r.rule_group, "
    "r.severity, r.reason, r.evidence, r.evidence_key, :as_of "
    "FROM jsonb_to_recordset(:rows) AS r(person_id integer, config_id text, "
    "config_version integer, rule_id text, rule_group text, severity text, reason text, "
    "evidence jsonb, evidence_key text) "
    "ON CONFLICT (tenant_id, person_id, evidence_key) DO UPDATE SET cleared_at = NULL "
    "WHERE app.surveillance_flag.cleared_at IS NOT NULL "
    "AND app.surveillance_flag.status <> 'resolved' "
    "RETURNING (xmax = 0) AS inserted"
).bindparams(bindparam("rows", type_=JSONB))
CLEAR = text(
    "UPDATE app.surveillance_flag SET cleared_at = now() "
    "WHERE cleared_at IS NULL AND status <> 'resolved' AND as_of <= :as_of "
    "AND config_id = ANY(:configs) "
    "AND (person_id, evidence_key) NOT IN "
    "(SELECT * FROM unnest(CAST(:persons AS integer[]), CAST(:keys AS text[])))"
)


def codes(config: SurveillanceConfig, *, source: bool) -> dict[str, tuple[str, str]]:
    """{marker: (vocabulary, code)} for markers matched on the source (or standard) concept."""
    return {k: (m.vocabulary, m.code) for k, m in config.markers.items()
            if m.source_concept == source}  # fmt: skip


def load_series(
    conn: Connection, config: SurveillanceConfig, as_of: date
) -> dict[int, PatientSeries]:
    """Series of every patient of the caller's center in a transplant program, up to `as_of`."""
    if config.anchor != TRANSPLANT_ANCHOR:
        raise ValueError(f"{config.id}: only transplant programs are loaded from cdm")
    patients = conn.execute(PATIENTS, {**FACTS_PARAMS, "program": config.program}).all()
    standard = concept_keys(conn, codes(config, source=False))
    source = concept_keys(conn, codes(config, source=True))
    values: dict[int, dict[str, list[Observation]]] = defaultdict(lambda: defaultdict(list))
    rows = conn.execute(MEASUREMENTS, {
        "persons": [p.person_id for p in patients], "as_of": as_of,
        "standard": list(standard), "source": list(source),
    })  # fmt: skip
    for r in rows:
        marker = standard.get(r.measurement_concept_id) or source.get(
            r.measurement_source_concept_id or 0
        )
        if marker is not None:
            values[r.person_id][marker].append(
                Observation(r.measurement_date, float(r.value_as_number))
            )
    return {
        p.person_id: PatientSeries(
            patient_key=str(p.person_id),
            program=config.program,
            anchor_date=p.transplant_date,
            end_date=min((d for d in (p.death_date, p.failure_date) if d), default=None),
            observations={m: tuple(obs) for m, obs in values[p.person_id].items()},
        )
        for p in patients
    }


def save_flags(
    conn: Connection,
    run_id: str,
    as_of: date,
    configs: Iterable[SurveillanceConfig],
    flags: Mapping[int, list[Flag]],
) -> tuple[int, int]:
    """Store this run's flags and clear older ones that no longer fire. Returns (opened, cleared).

    A flag already stored for the same evidence is left alone (a resolved one stays resolved);
    one the engine had cleared becomes active again unless it was resolved. Flags newer than
    `as_of` are never cleared, so replaying an old date cannot hide later flags.
    """
    rows = [
        {"person_id": person_id, "config_id": f.config_id, "config_version": f.config_version,
         "rule_id": f.rule_id, "rule_group": f.group, "severity": f.severity.value,
         "reason": f.reason, "evidence": [e.as_dict() for e in f.evidence],
         "evidence_key": f.evidence_key}
        for person_id, person_flags in flags.items() for f in person_flags
    ]  # fmt: skip
    opened = 0
    if rows:
        result = conn.execute(UPSERT, {"run_id": run_id, "as_of": as_of, "rows": rows})
        opened = sum(1 for r in result if r.inserted)
    cleared = conn.execute(CLEAR, {
        "as_of": as_of, "configs": [c.id for c in configs],
        "persons": [r["person_id"] for r in rows], "keys": [r["evidence_key"] for r in rows],
    }).rowcount  # fmt: skip
    return opened, cleared


def record_run(
    conn: Connection,
    run_id: str,
    as_of: date,
    configs: Iterable[SurveillanceConfig],
    patients: int,
    opened: int,
    cleared: int,
) -> None:
    """The run row: which config versions ran and what changed."""
    conn.execute(
        text(
            "INSERT INTO app.surveillance_run (run_id, as_of, configs, patients, flags_opened, "
            "flags_cleared) VALUES (:run_id, :as_of, :configs, :patients, :opened, :cleared)"
        ).bindparams(bindparam("configs", type_=JSONB)),
        {"run_id": run_id, "as_of": as_of, "configs": {c.id: c.version for c in configs},
         "patients": patients, "opened": opened, "cleared": cleared},
    )  # fmt: skip


class Status(StrEnum):
    """Where a flag is in the clinician's review."""

    NEW = "new"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


ALLOWED = {
    Status.NEW: {Status.ACKNOWLEDGED, Status.RESOLVED},
    Status.ACKNOWLEDGED: {Status.RESOLVED},
    Status.RESOLVED: set(),
}


class InvalidTransitionError(ValueError):
    """The flag cannot move from its current status to the requested one."""


@dataclass(frozen=True)
class StatusChange:
    """What a status change did, for the audit log."""

    flag_id: str
    person_id: int
    old: Status
    new: Status
    changed_at: datetime


def set_status(
    conn: Connection, flag_id: str, new: Status, user_id: str, note: str | None
) -> StatusChange | None:
    """Move a flag to `new`; None if there is no such flag in this center.

    Raises InvalidTransitionError for a move the workflow does not allow (e.g. reopening a
    resolved flag). The row is locked, so two clinicians cannot both change it at once.
    """
    row = conn.execute(
        text("SELECT person_id, status FROM app.surveillance_flag WHERE flag_id = :id FOR UPDATE"),
        {"id": flag_id},
    ).one_or_none()
    if row is None:
        return None
    old = Status(row.status)
    if new not in ALLOWED[old]:
        raise InvalidTransitionError(f"cannot change a {old} flag to {new}")
    changed_at = conn.execute(
        text(
            "UPDATE app.surveillance_flag SET status = :status, status_changed_by = :user, "
            "status_changed_at = now(), note = COALESCE(:note, note) WHERE flag_id = :id "
            "RETURNING status_changed_at"
        ),
        {"status": new.value, "user": user_id, "note": note, "id": flag_id},
    ).scalar_one()
    return StatusChange(flag_id, row.person_id, old, new, changed_at)
