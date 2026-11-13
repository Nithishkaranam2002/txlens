"""OMOP -> one row per surveillance visit: features, labels and the rule engine's alert.

Runs inside a tenant transaction, so RLS limits it to one center. Rejection results come from
two OMOP sources: pathology reports (dated on the biopsy) and clinic notes (dated when the note
was written, usually days later). A note result counts only when a biopsy precedes it by at
most 14 days ("biopsy-proven"); it is tied to that biopsy, and the earliest record of a
biopsy's result is the day it became known.
"""

from collections import defaultdict
from collections.abc import Iterable
from datetime import date, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import Connection, text

from txlens_backend.clinical_codes import BIOPSY, DSA_LOCAL, LABS, REJECTION
from txlens_backend.rejection import rejection_concept_ids
from txlens_backend.risk.features import FEATURE_NAMES, features_at
from txlens_backend.risk.labels import label, shortly_before_biopsy
from txlens_backend.risk.record import BiopsyResult, Finding, PatientRecord
from txlens_backend.risk.visits import scheduled_visits
from txlens_backend.services.facts import FACTS_CTE, FACTS_PARAMS, descendant_ids
from txlens_backend.surveillance import repository
from txlens_backend.surveillance.config import Marker, Severity, SurveillanceConfig
from txlens_backend.surveillance.engine import evaluate
from txlens_backend.surveillance.model import PatientSeries
from txlens_backend.survival.extract import DGF, HISTORY_OF_EVENT, concept_id

ATTRIBUTION = timedelta(days=14)  # a note's rejection result belongs to a biopsy this recent
ALERT = {Severity.HIGH, Severity.MEDIUM}  # same "alert" as the Step 1.6 evaluation
FEATURE_MARKERS = {
    "dd_cfdna": Marker(label="dd-cfDNA", vocabulary=LABS["dd_cfdna"][0],
                       code=LABS["dd_cfdna"][1], unit="%"),
    "creatinine": Marker(label="Creatinine", vocabulary=LABS["creatinine"][0],
                         code=LABS["creatinine"][1], unit="mg/dL"),
    "tacrolimus_trough": Marker(label="Tacrolimus trough", vocabulary=LABS["tacrolimus_trough"][0],
                                code=LABS["tacrolimus_trough"][1], unit="ng/mL"),
    "dsa_mfi": Marker(label="DSA (max MFI)", vocabulary=DSA_LOCAL[0], code=DSA_LOCAL[1],
                      unit="MFI", source_concept=True),
}  # fmt: skip
META = ["key", "person_id", "tenant", "organ", "sex", "age", "transplant_year", "visit",
        "label", "label_borderline", "rule_alert", "pre_biopsy"]  # fmt: skip
COLUMNS = META + list(FEATURE_NAMES)

FACTS_SQL = f"""
WITH {FACTS_CTE},
followup AS (
    SELECT person_id, max(observation_period_end_date) AS end_date
    FROM cdm.observation_period GROUP BY person_id
),
dgf AS (
    SELECT person_id, min(observation_date) AS known_day FROM cdm.observation
    WHERE observation_concept_id = :history_of_event AND value_as_concept_id = ANY(:dgf_ids)
    GROUP BY person_id
)
SELECT f.person_id, f.organ, f.donor_type, f.sex, f.year_of_birth, f.transplant_date,
       fu.end_date AS follow_up_end, dgf.known_day AS dgf_known_day
FROM facts f
JOIN followup fu ON fu.person_id = f.person_id
LEFT JOIN dgf ON dgf.person_id = f.person_id
WHERE f.transplant_date IS NOT NULL
"""
BIOPSIES_SQL = (
    "SELECT person_id, procedure_date FROM cdm.procedure_occurrence "
    "WHERE procedure_concept_id = ANY(:biopsy_ids) ORDER BY person_id, procedure_date"
)
REJECTIONS_SQL = (
    "SELECT person_id, condition_start_date AS day, "
    "(condition_concept_id = 0 AND condition_source_concept_id = :borderline) AS borderline "
    "FROM cdm.condition_occurrence "
    "WHERE condition_concept_id = ANY(:rejection_ids) "
    "OR condition_source_concept_id = ANY(:rejection_ids)"
)


def feature_config(config: SurveillanceConfig) -> SurveillanceConfig:
    """The program's config with every marker the features need (the rules are unchanged)."""
    return config.model_copy(update={"markers": {**config.markers, **FEATURE_MARKERS}})


def biopsy_results(
    biopsies: list[date], evidence: Iterable[tuple[date, bool]]
) -> tuple[BiopsyResult, ...]:
    """Tie each rejection record (day, is_borderline) to the latest biopsy at most 14 days
    earlier; a biopsy with both a rejection and a borderline record counts as rejection."""
    found: dict[date, dict[Finding, date]] = defaultdict(dict)
    for day, borderline in evidence:
        biopsy = max((b for b in biopsies if b <= day <= b + ATTRIBUTION), default=None)
        if biopsy is None:
            continue
        finding = Finding.BORDERLINE if borderline else Finding.REJECTION
        found[biopsy][finding] = min(found[biopsy].get(finding, day), day)
    results = []
    for biopsy, by_finding in sorted(found.items()):
        finding = Finding.REJECTION if Finding.REJECTION in by_finding else Finding.BORDERLINE
        results.append(BiopsyResult(biopsy, by_finding[finding], finding))
    return tuple(results)


def lookup_params(conn: Connection) -> dict[str, Any]:
    """Concept sets the queries need."""
    dgf = concept_id(conn, *DGF)
    biopsy = [concept_id(conn, *code) for code in BIOPSY.values()]
    return dict(FACTS_PARAMS) | {
        "history_of_event": concept_id(conn, *HISTORY_OF_EVENT),
        "dgf_ids": sorted(descendant_ids(conn, [dgf])) if dgf else [],
        "biopsy_ids": sorted(descendant_ids(conn, [b for b in biopsy if b])),
        "rejection_ids": sorted(rejection_concept_ids(conn)),
        "borderline": concept_id(conn, *REJECTION[1]),
    }


def load_records(
    conn: Connection, tenant: str, configs: Iterable[SurveillanceConfig], as_of: date
) -> list[tuple[PatientRecord, SurveillanceConfig]]:
    """Every transplant recipient of the caller's center with the config of their program."""
    params = lookup_params(conn)
    facts = {r.person_id: r for r in conn.execute(text(FACTS_SQL), params)}
    biopsies: dict[int, list[date]] = defaultdict(list)
    for r in conn.execute(text(BIOPSIES_SQL), params):
        if r.procedure_date <= as_of:
            biopsies[r.person_id].append(r.procedure_date)
    evidence: dict[int, list[tuple[date, bool]]] = defaultdict(list)
    for r in conn.execute(text(REJECTIONS_SQL), params):
        if r.day <= as_of:
            evidence[r.person_id].append((r.day, bool(r.borderline)))
    records = []
    for config in configs:
        series = repository.load_series(conn, feature_config(config), as_of)
        for person_id, s in sorted(series.items()):
            f = facts.get(person_id)
            if f is None:
                continue
            record = PatientRecord(
                key=f"{tenant}:{person_id}", tenant=tenant, organ=f.organ,
                donor_type=f.donor_type, sex=f.sex, birth_year=f.year_of_birth,
                transplant_day=s.anchor_date, follow_up_end=min(f.follow_up_end, as_of),
                end_day=s.end_date, observations=dict(s.observations),
                biopsy_days=tuple(biopsies[person_id]),
                results=biopsy_results(biopsies[person_id], evidence[person_id]),
                dgf_known_day=f.dgf_known_day,
            )  # fmt: skip
            records.append((record, config))
    return records


def series_of(record: PatientRecord, config: SurveillanceConfig) -> PatientSeries:
    """The record as the rule engine reads it."""
    return PatientSeries(record.key, config.program, record.transplant_day, record.end_day,
                         record.observations)  # fmt: skip


def visit_rows(
    record: PatientRecord, config: SurveillanceConfig, as_of: date
) -> list[dict[str, Any]]:
    """One row per surveillance visit up to `as_of` (labelled or not)."""
    if config.schedule is None:
        return []
    series = series_of(record, config)
    rows = []
    for visit in scheduled_visits(record, config.schedule, as_of):
        flags = evaluate(config, series, visit)
        rows.append({
            "key": record.key, "person_id": int(record.key.split(":")[1]),
            "tenant": record.tenant, "organ": record.organ, "sex": record.sex,
            "age": (record.transplant_day.year - record.birth_year) if record.birth_year else None,
            "transplant_year": record.transplant_day.year, "visit": visit,
            "label": label(record, visit),
            "label_borderline": label(record, visit, borderline=True),
            "rule_alert": any(f.severity in ALERT for f in flags),
            "pre_biopsy": shortly_before_biopsy(record, visit),
            **features_at(record, config.schedule, visit),
        })  # fmt: skip
    return rows


def build(
    conn: Connection, tenant: str, configs: Iterable[SurveillanceConfig], as_of: date
) -> pd.DataFrame:
    """Every surveillance visit of the caller's center up to `as_of`."""
    rows = [row for record, config in load_records(conn, tenant, configs, as_of)
            for row in visit_rows(record, config, as_of)]  # fmt: skip
    return pd.DataFrame(rows, columns=COLUMNS)
