"""Scoring: the champion model on each patient's latest surveillance visit, stored with its version.

Used by the nightly surveillance workflow (every patient of a center) and by the API (one
patient, on demand). The score is decision support for review, never a diagnosis: the band is
"elevated" when the score is at or above the version's alert threshold, which alerts on as many
visits as the rule engine did in the training data.
"""

import json
import math
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import Connection, Engine, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.expression import bindparam

from txlens_backend import audit
from txlens_backend.db import tenant_transaction
from txlens_backend.risk.dataset import load_records, visit_rows
from txlens_backend.risk.explain import top_contributions
from txlens_backend.risk.features import FEATURE_NAMES
from txlens_backend.risk.metrics import psi_against
from txlens_backend.risk.registry import Champion
from txlens_backend.surveillance.run import transplant_configs

TOP_K = 3
INSERT = text(
    "INSERT INTO app.risk_prediction (person_id, visit_date, model_name, model_version, score, "
    "threshold, band, contributions, features, source, run_id, scored_by) "
    "SELECT r.person_id, r.visit_date, r.model_name, r.model_version, r.score, r.threshold, "
    "r.band, r.contributions, r.features, r.source, r.run_id, r.scored_by "
    "FROM jsonb_to_recordset(:rows) AS r(person_id integer, visit_date date, model_name text, "
    "model_version text, score double precision, threshold double precision, band text, "
    "contributions jsonb, features jsonb, source text, run_id uuid, scored_by uuid) "
    "ON CONFLICT (tenant_id, person_id, visit_date, model_name, model_version) DO NOTHING"
).bindparams(bindparam("rows", type_=JSONB))
LATEST = text(
    "SELECT prediction_id, person_id, visit_date, model_name, model_version, score, threshold, "
    "band, contributions, features, source, created_at FROM app.risk_prediction "
    "WHERE person_id = :person ORDER BY visit_date DESC, created_at DESC LIMIT 1"
)


@dataclass(frozen=True)
class Prediction:
    """One scored visit, with everything needed to trace it."""

    person_id: int
    visit_date: date
    model_name: str
    model_version: str
    score: float
    threshold: float
    band: str
    contributions: list[dict[str, Any]]
    features: dict[str, float | None]

    def row(self, source: str, run_id: str | None, scored_by: str | None) -> dict[str, Any]:
        return {"person_id": self.person_id, "visit_date": self.visit_date.isoformat(),
                "model_name": self.model_name, "model_version": self.model_version,
                "score": self.score, "threshold": self.threshold, "band": self.band,
                "contributions": self.contributions, "features": self.features,
                "source": source, "run_id": run_id, "scored_by": scored_by}  # fmt: skip


def latest_visits(
    conn: Connection, tenant: str, as_of: date, person_id: int | None = None
) -> pd.DataFrame:
    """Each patient's latest surveillance visit up to `as_of`, for patients still under
    surveillance (alive with a functioning graft)."""
    rows = []
    for record, config in load_records(conn, tenant, transplant_configs(), as_of):
        if person_id is not None and record.key != f"{tenant}:{person_id}":
            continue
        visits = visit_rows(record, config, as_of)
        if visits and visits[-1]["active"]:
            rows.append(visits[-1])
    return pd.DataFrame(rows)


def clean(value: float) -> float | None:
    return None if value is None or math.isnan(value) else float(value)


def score(champion: Champion, visits: pd.DataFrame) -> list[Prediction]:
    """Score visits with the champion: probability, band and top contributions."""
    if visits.empty:
        return []
    x = visits[list(FEATURE_NAMES)].astype(float)
    p = champion.model.predict_proba(x)[:, 1]
    out = []
    for i, (person_id, visit) in enumerate(zip(visits["person_id"], visits["visit"], strict=True)):
        row = x.iloc[[i]]
        out.append(Prediction(
            person_id=int(person_id), visit_date=visit, model_name=champion.name,
            model_version=champion.version, score=float(p[i]), threshold=champion.threshold,
            band="elevated" if p[i] >= champion.threshold else "lower",
            contributions=[c.as_dict() for c in top_contributions(champion.model, row, TOP_K)],
            features={f: clean(row.iloc[0][f]) for f in FEATURE_NAMES},
        ))  # fmt: skip
    return out


MIN_FOR_DRIFT = 50  # fewer scored patients than this: too few to judge drift


def score_psi(champion: Champion, predictions: list[Prediction]) -> float | None:
    """Drift of tonight's scores against the same population at training time."""
    if len(predictions) < MIN_FOR_DRIFT:
        return None
    return psi_against(champion.reference.score, np.array([p.score for p in predictions]))


def feature_psi(champion: Champion, predictions: list[Prediction]) -> dict[str, float]:
    """Drift of each input against the same population at training time."""
    if len(predictions) < MIN_FOR_DRIFT:
        return {}
    return {
        name: psi_against(bins, np.array([np.nan if p.features[name] is None
                                          else p.features[name] for p in predictions]))
        for name, bins in champion.reference.features.items()
    }  # fmt: skip


def store(
    conn: Connection,
    predictions: list[Prediction],
    source: str,
    run_id: str | None = None,
    scored_by: str | None = None,
) -> int:
    """Insert predictions (a visit already scored by the same version keeps its first row)."""
    if not predictions:
        return 0
    rows = [p.row(source, run_id, scored_by) for p in predictions]
    return conn.execute(INSERT, {"rows": json.loads(json.dumps(rows))}).rowcount


def record_run(
    conn: Connection, champion: Champion, as_of: date, predictions: list[Prediction]
) -> str:
    """The nightly run row; returns its id."""
    return str(conn.execute(
        text("INSERT INTO app.risk_run (as_of, model_name, model_version, scored, score_psi, "
             "feature_psi) VALUES (:as_of, :name, :version, :scored, :psi, :features) "
             "RETURNING run_id").bindparams(bindparam("features", type_=JSONB)),
        {"as_of": as_of, "name": champion.name, "version": champion.version,
         "scored": len(predictions), "psi": score_psi(champion, predictions),
         "features": feature_psi(champion, predictions)},
    ).scalar_one())  # fmt: skip


def latest(conn: Connection, person_id: int) -> Any:
    """The newest stored prediction for a patient (None if never scored)."""
    return conn.execute(LATEST, {"person": person_id}).one_or_none()


@dataclass(frozen=True)
class ScoringRun:
    """What one center's nightly scoring did."""

    run_id: str
    model_version: str
    scored: int
    score_psi: float | None


def score_tenant(engine: Engine, tenant_id: str, as_of: date, champion: Champion) -> ScoringRun:
    """Score every patient's latest visit in one center and store the run (one transaction)."""
    with tenant_transaction(engine, tenant_id) as conn:
        predictions = score(champion, latest_visits(conn, tenant_id, as_of))
        run_id = record_run(conn, champion, as_of, predictions)
        stored = store(conn, predictions, "nightly", run_id=run_id)
        psi = score_psi(champion, predictions)
        audit.record(
            conn, audit.RequestInfo(request_id=run_id, ip=""), None, "risk_scoring_run",
            "risk_run", run_id,
            {"as_of": as_of.isoformat(), "model": champion.name, "version": champion.version,
             "scored": len(predictions), "stored": stored, "score_psi": psi},
        )  # fmt: skip
    return ScoringRun(run_id, champion.version, len(predictions), psi)
