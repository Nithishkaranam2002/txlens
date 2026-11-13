"""Clinician worklist and patient flags, shaped for the viewer.

Clinicians see identified patients and flags as stored. Data stewards see pseudonyms, and every
date in a flag (evidence, reason text, as-of, status change) moves by the patient's date shift;
the clinician's free-text note and the reviewer's user id are hidden, since free text can name
people.
"""

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import Connection, Row, text

from txlens_backend.services import patients as pts
from txlens_backend.surveillance.config import DEFAULT_WEIGHTS, Severity, load_configs
from txlens_backend.surveillance.engine import priority
from txlens_backend.surveillance.repository import Status

ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
FLAG_COLUMNS = (
    "flag_id::text AS flag_id, person_id, config_id, config_version, rule_id, rule_group, "
    "severity, reason, evidence, as_of, status, status_changed_by::text AS status_changed_by, "
    "status_changed_at, note, cleared_at"
)
OPEN = "status <> 'resolved' AND cleared_at IS NULL"


class EvidenceItem(BaseModel):
    """A result (or due date) a flag is based on."""

    marker: str
    date: date
    value: float | None
    role: str


class FlagView(BaseModel):
    """A reason to review a patient. Clinical decision support, never a diagnosis."""

    flag_id: str
    rule_id: str
    config_id: str
    config_version: int
    severity: Severity
    reason: str
    evidence: list[EvidenceItem]
    as_of: date
    status: Status
    status_changed_at: datetime | None
    status_changed_by: str | None
    note: str | None
    cleared: bool


class FlagEvent(BaseModel):
    """One step in a flag's life: raised by the engine, reviewed by a clinician, or cleared."""

    at: datetime
    event: Literal["raised", "acknowledged", "resolved", "cleared"]
    by: str | None  # reviewer's display name; clinicians only
    note: str | None  # clinicians only


class PatientFlag(FlagView):
    """A flag with its status history, oldest event first."""

    history: list[FlagEvent]


class WorklistRow(BaseModel):
    """One patient on the worklist with their open flags, highest priority first."""

    patient: pts.IdentifiedPatient | pts.DeidentifiedPatient
    score: float
    flags: list[FlagView]


class PatientFlags(BaseModel):
    """All flags of one patient, open and closed, newest first, each with its history."""

    patient: pts.IdentifiedPatient | pts.DeidentifiedPatient
    flags: list[PatientFlag]


def shift_text(reason: str, move: timedelta) -> str:
    """The reason with every ISO date moved."""
    return ISO_DATE.sub(lambda m: (date.fromisoformat(m.group(0)) + move).isoformat(), reason)


def flag_view(row: Row[tuple[object, ...]], viewer: pts.Viewer, tenant_id: str) -> FlagView:
    """A stored flag as this viewer may see it."""
    m = row._mapping
    move = timedelta(days=viewer.shift_days(tenant_id, m["person_id"]))
    changed_at = m["status_changed_at"]
    return FlagView(
        flag_id=m["flag_id"], rule_id=m["rule_id"], config_id=m["config_id"],
        config_version=m["config_version"], severity=Severity(m["severity"]),
        reason=shift_text(m["reason"], move),
        evidence=[EvidenceItem(marker=e["marker"], date=date.fromisoformat(e["date"]) + move,
                               value=e["value"], role=e["role"]) for e in m["evidence"]],
        as_of=m["as_of"] + move, status=Status(m["status"]),
        status_changed_at=changed_at + move if changed_at else None,
        status_changed_by=m["status_changed_by"] if viewer.identified else None,
        note=m["note"] if viewer.identified else None,
        cleared=m["cleared_at"] is not None,
    )  # fmt: skip


def patients_by_id(
    conn: Connection, viewer: pts.Viewer, person_ids: list[int]
) -> dict[int, pts.IdentifiedPatient | pts.DeidentifiedPatient]:
    """The patient summaries for these people, keyed by person_id."""
    params = pts.query_params(viewer, pts.Filters()) | {"ids": person_ids}
    tail = "AND f.person_id = ANY(:ids)"
    if viewer.identified:
        select = "f.*, i.mrn, i.given_name, i.family_name, i.birth_date"
        rows = conn.execute(text(pts.identified_query(select, tail)), params).all()
        return {r.person_id: pts.identified(r) for r in rows}
    rows = conn.execute(text(pts.deidentified_query("f.*", tail)), params).all()
    return {r.person_id: pts.deidentified(r, viewer) for r in rows}


SEVERITY_ORDER = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}


@dataclass(frozen=True)
class WorklistFilters:
    """Keep rows of this organ whose highest open severity is one of `severities` and that
    have at least one open flag in this status."""

    organ: pts.Organ | None = None
    severities: frozenset[Severity] | None = None
    status: Status | None = None

    def keep(self, row: WorklistRow) -> bool:
        """True if the row passes every filter that is set."""
        if self.organ is not None and row.patient.organ != self.organ:
            return False
        top = min((flag.severity for flag in row.flags), key=SEVERITY_ORDER.__getitem__)
        if self.severities is not None and top not in self.severities:
            return False
        return self.status is None or any(flag.status is self.status for flag in row.flags)


def data_as_of(conn: Connection) -> date | None:
    """The data date of the center's latest surveillance run (None before the first run)."""
    latest: date | None = conn.execute(
        text("SELECT max(as_of) FROM app.surveillance_run")
    ).scalar_one()
    return latest


def new_flag_ids(conn: Connection, person_id: int) -> list[str]:
    """The patient's open flags nobody has acknowledged yet."""
    return list(conn.execute(text(
        "SELECT flag_id::text FROM app.surveillance_flag WHERE person_id = :p "
        "AND status = 'new' AND cleared_at IS NULL ORDER BY created_at, rule_id"),
        {"p": person_id}).scalars())  # fmt: skip


def worklist(
    conn: Connection, viewer: pts.Viewer, tenant_id: str, filters: WorklistFilters | None = None
) -> list[WorklistRow]:
    """Every patient with an open flag (passing the filters), highest priority first.

    Ties go to the patient whose newest flag is most recent, then to the patient id (the
    pseudonym for de-identified viewers).
    """
    rows = conn.execute(text(
        f"SELECT {FLAG_COLUMNS} FROM app.surveillance_flag WHERE {OPEN} "
        "ORDER BY person_id, created_at DESC, rule_id"
    )).all()  # fmt: skip
    by_person: dict[int, list[Row[tuple[object, ...]]]] = defaultdict(list)
    for row in rows:
        by_person[row.person_id].append(row)
    configs = load_configs()
    people = patients_by_id(conn, viewer, list(by_person))
    ranked = []
    for person_id, flags in by_person.items():
        # A patient is in one program, so all their flags come from one config.
        config = configs.get(flags[0].config_id)
        score = priority(((Severity(f.severity), f.rule_group) for f in flags),
                         config.severity_weights if config else DEFAULT_WEIGHTS)  # fmt: skip
        views = sorted((flag_view(f, viewer, tenant_id) for f in flags),
                       key=lambda v: (SEVERITY_ORDER[v.severity], v.rule_id))  # fmt: skip
        newest = max(f.as_of for f in flags)
        patient = people[person_id]
        ranked.append(((-score, -newest.toordinal(), str(patient.patient_id)),
                       WorklistRow(patient=patient, score=score, flags=views)))  # fmt: skip
    keep = (filters or WorklistFilters()).keep
    return [row for _, row in sorted(ranked, key=lambda r: r[0]) if keep(row)]


def history(
    conn: Connection, viewer: pts.Viewer, tenant_id: str, flag_rows: Sequence[Row[Any]]
) -> dict[str, list[FlagEvent]]:
    """flag_id -> its events. Reviews come from the audit log, which records every status
    change with its old and new status and note; times shift with the patient's dates."""
    reviews = conn.execute(
        text(
            "SELECT a.object_id, a.occurred_at, a.details->>'to' AS status, "
            "a.details->>'note' AS note, u.display_name "
            "FROM app.audit_log a LEFT JOIN app.app_user u ON u.user_id = a.user_id "
            "WHERE a.object_type = 'surveillance_flag' "
            "AND a.action IN ('flag_acknowledge', 'flag_resolve') AND a.object_id = ANY(:ids) "
            "ORDER BY a.occurred_at, a.audit_id"
        ),
        {"ids": [row.flag_id for row in flag_rows]},
    ).all()
    events: dict[str, list[FlagEvent]] = {}
    for row in flag_rows:
        move = timedelta(days=viewer.shift_days(tenant_id, row.person_id))
        raised = [FlagEvent(at=row.created_at + move, event="raised", by=None, note=None)]
        reviewed = [
            FlagEvent(at=r.occurred_at + move, event=r.status,
                      by=r.display_name if viewer.identified else None,
                      note=r.note if viewer.identified else None)
            for r in reviews if r.object_id == row.flag_id
        ]  # fmt: skip
        cleared = (
            [FlagEvent(at=row.cleared_at + move, event="cleared", by=None, note=None)]
            if row.cleared_at else []
        )  # fmt: skip
        events[row.flag_id] = raised + reviewed + cleared
    return events


def patient_flags(
    conn: Connection, viewer: pts.Viewer, tenant_id: str, person_id: int
) -> PatientFlags:
    """All flags of one patient (open, cleared and resolved), newest first."""
    rows = conn.execute(
        text(f"SELECT {FLAG_COLUMNS}, created_at FROM app.surveillance_flag "
             "WHERE person_id = :p ORDER BY as_of DESC, created_at DESC, rule_id"),
        {"p": person_id},
    ).all()  # fmt: skip
    patient = patients_by_id(conn, viewer, [person_id])[person_id]
    events = history(conn, viewer, tenant_id, rows)
    flags = [
        PatientFlag(**flag_view(r, viewer, tenant_id).model_dump(), history=events[r.flag_id])
        for r in rows
    ]
    return PatientFlags(patient=patient, flags=flags)


def one_flag(conn: Connection, viewer: pts.Viewer, tenant_id: str, flag_id: str) -> FlagView:
    """One stored flag (after a status change)."""
    row = conn.execute(
        text(f"SELECT {FLAG_COLUMNS} FROM app.surveillance_flag WHERE flag_id = :f"),
        {"f": flag_id},
    ).one()
    return flag_view(row, viewer, tenant_id)
