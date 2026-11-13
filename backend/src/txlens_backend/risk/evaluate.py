"""The evaluation numbers the model card and the registry share (the eval report adds more).

Everything is computed on the internal and external test sets of one development run, with
95% intervals from resampling patients. Groups with fewer than 11 rejections are not scored.
"""

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from txlens_backend.risk.develop import Development, score_column
from txlens_backend.risk.metrics import (
    Interval,
    auprc,
    auroc,
    brier,
    calibration,
    cluster_bootstrap,
    threshold_for_rate,
)
from txlens_backend.risk.models import Kind

MIN_EVENTS = 11  # small-cell rule: a group needs this many rejections to be scored
AGE_BANDS = ((18, 39, "18-39"), (40, 59, "40-59"), (60, 200, "60+"))


def age_band(age: float) -> str:
    """The fairness age band of an age at transplant."""
    return next((name for low, high, name in AGE_BANDS if low <= age <= high), "unknown")


@dataclass(frozen=True)
class TestScores:
    """One model on one test set."""

    n_visits: int
    n_patients: int
    n_events: int
    prevalence: float
    auroc: Interval
    auprc: Interval
    brier: Interval
    calibration_intercept: float
    calibration_slope: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_visits": self.n_visits, "n_patients": self.n_patients, "n_events": self.n_events,
            "prevalence": self.prevalence, "auroc": self.auroc.as_dict(),
            "auprc": self.auprc.as_dict(), "brier": self.brier.as_dict(),
            "calibration_intercept": self.calibration_intercept,
            "calibration_slope": self.calibration_slope,
        }  # fmt: skip


def test_scores(frame: pd.DataFrame, kind: Kind, label: str) -> TestScores:
    y = frame[label].to_numpy(dtype=float)
    p = frame[score_column(kind)].to_numpy(dtype=float)
    keys = list(frame["key"])
    intercept, slope = calibration(y, p)
    return TestScores(
        n_visits=len(frame), n_patients=frame["key"].nunique(), n_events=int(y.sum()),
        prevalence=float(y.mean()),
        auroc=cluster_bootstrap(keys, [y, p], auroc), auprc=cluster_bootstrap(keys, [y, p], auprc),
        brier=cluster_bootstrap(keys, [y, p], brier),
        calibration_intercept=intercept, calibration_slope=slope,
    )  # fmt: skip


@dataclass(frozen=True)
class BurdenComparison:
    """The model and the rule engine alerting on the same share of visits."""

    alert_rate: float
    rule_sensitivity: Interval
    model_sensitivity: Interval
    difference: Interval  # model - rule, paired by patient
    rule_ppv: float
    model_ppv: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "alert_rate": self.alert_rate, "rule_sensitivity": self.rule_sensitivity.as_dict(),
            "model_sensitivity": self.model_sensitivity.as_dict(),
            "difference": self.difference.as_dict(), "rule_ppv": self.rule_ppv,
            "model_ppv": self.model_ppv,
        }  # fmt: skip


def sensitivity(y: np.ndarray, alert: np.ndarray) -> float:
    return float(alert[y == 1].mean()) if y.sum() > 0 else float("nan")


def ppv(y: np.ndarray, alert: np.ndarray) -> float:
    return float(y[alert].mean()) if alert.sum() > 0 else float("nan")


def equal_burden(frame: pd.DataFrame, kind: Kind, label: str) -> BurdenComparison:
    """Model alerts on the top-scored share of visits that the rule engine alerts on."""
    y = frame[label].to_numpy(dtype=float)
    p = frame[score_column(kind)].to_numpy(dtype=float)
    rule = frame["rule_alert"].to_numpy(dtype=bool)
    rate = float(rule.mean())
    model = p >= threshold_for_rate(p, rate)
    keys = list(frame["key"])
    return BurdenComparison(
        alert_rate=rate,
        rule_sensitivity=cluster_bootstrap(keys, [y, rule], sensitivity),
        model_sensitivity=cluster_bootstrap(keys, [y, model], sensitivity),
        difference=cluster_bootstrap(
            keys, [y, model, rule], lambda a, m, r: sensitivity(a, m) - sensitivity(a, r)
        ),
        rule_ppv=ppv(y, rule), model_ppv=ppv(y, model),
    )  # fmt: skip


def fairness(frame: pd.DataFrame, kind: Kind, label: str) -> list[dict[str, Any]]:
    """AUROC, Brier and alert rate per age band and sex (scored only with >= 11 rejections)."""
    p = frame[score_column(kind)].to_numpy(dtype=float)
    cut = threshold_for_rate(p, float(frame["rule_alert"].mean()))
    data = frame.assign(age_band=frame["age"].map(age_band), alert=p >= cut)
    rows = []
    for column in ("age_band", "sex"):
        for group, part in data.groupby(column, sort=True):
            y = part[label].to_numpy(dtype=float)
            q = part[score_column(kind)].to_numpy(dtype=float)
            scored = y.sum() >= MIN_EVENTS
            rows.append({
                "by": column, "group": str(group), "n_visits": len(part),
                "n_events": int(y.sum()),
                "auroc": auroc(y, q) if scored else None,
                "brier": brier(y, q) if scored else None,
                "alert_rate": float(part["alert"].mean()),
                "sensitivity": sensitivity(y, part["alert"].to_numpy()) if scored else None,
            })  # fmt: skip
    return rows


@dataclass(frozen=True)
class Summary:
    """The shared evaluation of one development run."""

    label: str
    champion: Kind
    internal: dict[Kind, TestScores]
    external: dict[Kind, TestScores]
    burden_internal: BurdenComparison
    burden_external: BurdenComparison
    fairness: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label, "champion": self.champion.value,
            "internal": {k.value: v.as_dict() for k, v in self.internal.items()},
            "external": {k.value: v.as_dict() for k, v in self.external.items()},
            "burden_internal": self.burden_internal.as_dict(),
            "burden_external": self.burden_external.as_dict(),
            "fairness": self.fairness,
        }  # fmt: skip


def summarize(development: Development) -> Summary:
    """Champion and challenger on both test sets; burden and fairness for the champion."""
    label, kind = development.label, development.champion
    tests = pd.concat([development.internal, development.external], ignore_index=True)
    return Summary(
        label=label, champion=kind,
        internal={k: test_scores(development.internal, k, label) for k in Kind},
        external={k: test_scores(development.external, k, label) for k in Kind},
        burden_internal=equal_burden(development.internal, kind, label),
        burden_external=equal_burden(development.external, kind, label),
        fairness=fairness(tests, kind, label),
    )  # fmt: skip
