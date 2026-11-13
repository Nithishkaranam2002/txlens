"""Hand-built patient records for risk model tests."""

from datetime import date, timedelta

import numpy as np
import pandas as pd

from txlens_backend.risk.card import render
from txlens_backend.risk.develop import develop, production_model
from txlens_backend.risk.evaluate import summarize
from txlens_backend.risk.record import BiopsyResult, Finding, PatientRecord
from txlens_backend.risk.registry import register
from txlens_backend.surveillance.config import Schedule
from txlens_backend.surveillance.model import Observation

TX = date(2024, 1, 1)
SCHEDULE = Schedule(marker="dd_cfdna", months=(1, 2, 3), then_every_months=3, tolerance_days=14)


def day(n: int) -> date:
    """`n` days after transplant."""
    return TX + timedelta(days=n)


def obs(*points: tuple[int, float]) -> tuple[Observation, ...]:
    return tuple(Observation(day(d), v) for d, v in points)


def record(**changes: object) -> PatientRecord:
    """A kidney recipient followed for two years with a full set of labs."""
    base: dict[str, object] = {
        "key": "center_a:1", "tenant": "center_a", "organ": "kidney", "donor_type": "living",
        "sex": "female", "birth_year": 1980, "transplant_day": TX, "follow_up_end": day(730),
        "end_day": None,
        "observations": {
            "dd_cfdna": obs((30, 0.2), (61, 0.3), (91, 0.6), (183, 1.2), (274, 0.4)),
            "creatinine": obs((35, 1.0), (60, 1.2), (85, 1.1), (180, 1.65)),
            "tacrolimus_trough": obs((40, 8.0), (100, 10.0), (150, 6.0), (180, 8.0)),
            "dsa_mfi": obs((30, 200.0), (180, 1500.0)),
        },
        "biopsy_days": (day(200),),
        "results": (BiopsyResult(day(200), day(205), Finding.REJECTION),),
        "dgf_known_day": None,
    }  # fmt: skip
    return PatientRecord(**(base | changes))  # type: ignore[arg-type]


def visits(n_patients: int = 240, seed: int = 0) -> pd.DataFrame:
    """Fake visits across three centers in which a high dd-cfDNA raises the risk."""
    rng = np.random.default_rng(seed)
    rows = []
    for p in range(n_patients):
        tenant = ("center_a", "center_b", "center_c")[p % 3]
        for v in range(8):
            level = rng.normal(-1.6, 0.8)
            risk = 1 / (1 + np.exp(-(-4.2 + 1.6 * (level + 1.6))))
            rows.append({
                "key": f"{tenant}:{p}", "person_id": p, "tenant": tenant,
                "organ": "kidney", "sex": ("female", "male")[p % 2], "age": 20 + p % 55,
                "transplant_year": 2023, "visit": date(2024, 1, 1) + timedelta(days=30 * v),
                "label": float(rng.random() < risk), "label_borderline": 0.0,
                "rule_alert": bool(level > -0.5), "pre_biopsy": False, "active": True,
                "dd_cfdna_log": level, "dd_cfdna_slope": rng.normal(0, 0.1) if v else np.nan,
                "dd_cfdna_vs_min": np.nan if v == 0 else rng.uniform(1, 3),
                "creatinine_change_pct": rng.normal(0, 10), "tacrolimus_cv": rng.uniform(5, 40),
                "dsa_positive": float(rng.random() < 0.05), "months_since_transplant": v * 2.0,
                "prior_rejection": 0.0, "organ_heart": 0.0, "donor_deceased": 1.0, "dgf": 0.0,
            })  # fmt: skip
    return pd.DataFrame(rows)


def register_test_model(tracking_uri: str) -> str:
    """Develop on fake visits and register the result as champion; returns the version."""
    frame = visits()
    development = develop(frame)
    summary = summarize(development)
    return register(
        development, summary, production_model(development, frame), frame,
        lambda version: render(development, summary, frame, version, date(2026, 9, 30)),
        date(2026, 9, 30), tracking_uri,
    )  # fmt: skip
