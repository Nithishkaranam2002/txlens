"""The suppressed report: no small cells, pooled curve steps, landmarks, the methods text."""

import itertools
import re
from collections.abc import Iterator

import numpy as np
import pandas as pd
import pytest

from txlens_backend.survival.analysis import Report, Spec, analyse
from txlens_backend.survival.cox import COVARIATES
from txlens_backend.survival.extract import GRAFT_LOSS
from txlens_backend.survival.km import kaplan_meier
from txlens_backend.survival.landmark import GROUPINGS
from txlens_backend.survival.simulate import simulate
from txlens_backend.survival.suppression import Rule, pooled_points, read_off

THRESHOLD = 11
COUNT_FIELDS = {"n", "events", "n_risk", "n_exposed", "events_exposed"}


@pytest.fixture(scope="module")
def simulated() -> pd.DataFrame:
    return simulate()


def run(frame: pd.DataFrame, grouping: str, covariates: list[str] | None = None) -> Report:
    g = GROUPINGS[grouping]
    spec = Spec(
        "simulated",
        grouping,
        g.label,
        g.horizon_days,
        list(COVARIATES) if covariates is None else covariates,
        THRESHOLD,
    )
    return analyse(frame, g.assign(frame), spec)


def counts(value: object, key: str = "") -> Iterator[object]:
    """Every count anywhere in a dumped report (by field name, and log-rank observed)."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from (v.values() if k == "observed" and isinstance(v, dict) else counts(v, k))
    elif isinstance(value, list):
        for item in value:
            yield from counts(item, key)
    elif key in COUNT_FIELDS:
        yield value


@pytest.mark.parametrize("grouping", ["none", "donor_type", "age_band", "dsa_month1"])
def test_no_count_from_1_to_10_is_ever_shown(simulated: pd.DataFrame, grouping: str) -> None:
    small = simulated.sample(400, random_state=1)  # small groups, to provoke small cells
    dumped = run(small, grouping).model_dump()
    found = list(counts(dumped))
    assert found
    assert not [c for c in found if isinstance(c, int) and 0 < c < THRESHOLD]
    numbers = [int(m) for w in dumped["warnings"] for m in re.findall(r"\b\d+\b(?= patients)", w)]
    assert not [m for m in numbers if 0 < m < THRESHOLD]
    assert dumped["synthetic_data"] is True


def test_each_displayed_step_covers_at_least_11_events_while_11_are_at_risk(
    simulated: pd.DataFrame,
) -> None:
    curve = kaplan_meier(simulated["time"], (simulated["status"] == GRAFT_LOSS).astype(int))
    points = pooled_points(curve, 1.0, Rule(THRESHOLD, 0))
    t, events = curve.time, curve.n_event
    for before, after in itertools.pairwise(points):
        between = events[(t > before.day) & (t <= after.day)].sum()
        assert between >= THRESHOLD
        j = int(np.searchsorted(t, after.day))
        assert curve.n_risk[j] >= THRESHOLD
        assert after.estimate == pytest.approx(curve.estimate[j])  # the true value, not rounded
    assert read_off(points, points[1].day - 1) is None  # no step shown yet: no value either
    assert read_off(points, points[2].day - 1) == points[1]  # the last step by then, with its day
    assert read_off(points, points[-1].day + 1) is None  # nothing past the last shown step


def test_a_group_with_fewer_than_11_events_has_no_curve(simulated: pd.DataFrame) -> None:
    few = pd.concat(
        [
            simulated[simulated["status"] != GRAFT_LOSS].head(300),
            simulated[simulated["status"] == GRAFT_LOSS].head(5),
        ]
    )
    report = run(few, "none", covariates=[])
    (curve,) = report.km
    assert curve.points == [] and curve.hidden_reason == "fewer than 11 events"
    assert curve.events == "<11"
    assert report.cox is None


def test_groups_known_later_start_at_their_landmark(simulated: pd.DataFrame) -> None:
    report = run(simulated, "dd_cfdna_year1")
    assert report.landmark_days == 365
    assert all(curve.points[0].day == 365 for curve in report.km if curve.points)
    assert all(cell.day >= 365 for row in report.risk_table for cell in row.cells)
    assert "landmark of day 365" in report.methods


def test_cox_starts_at_the_month_1_dsa_test(simulated: pd.DataFrame) -> None:
    report = run(simulated, "donor_type")
    assert report.landmark_days == 0 and report.cox is not None
    assert report.cox.landmark_days == 30
    assert {t.key for t in report.cox.terms} == set(COVARIATES) - {"heart"}
    assert any(w.startswith("Cox model:") and "complete cases" in w for w in report.warnings)


def test_cumulative_incidence_never_exceeds_one_minus_km(simulated: pd.DataFrame) -> None:
    report = run(simulated, "donor_type")
    shown = [r for r in report.incidence_at if r.cumulative_incidence is not None]
    assert shown
    for row in shown:
        assert row.one_minus_km is not None and row.step_day is not None
        assert row.cumulative_incidence <= row.one_minus_km + 1e-12  # type: ignore[operator]
        assert row.step_day <= row.day


def test_methods_paragraph_states_the_design(simulated: pd.DataFrame) -> None:
    text = run(simulated, "dsa_month1").methods
    for phrase in (
        "Synthetic data",
        "death-censored graft loss",
        "Kaplan-Meier",
        "Greenwood",
        "log-rank",
        "Aalen-Johansen",
        "competing event",
        "Efron",
        "Schoenfeld",
        "DSA at month-1 test",
        '"<11"',
        "lifelines",
        "R survival",
    ):
        assert phrase in text, phrase
