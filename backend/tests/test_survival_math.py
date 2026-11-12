"""Kaplan-Meier, log-rank, Aalen-Johansen, Cox and the landmark designs (no database).

Exact values come from hand calculation; the simulated extract checks that known hazard ratios
are recovered and that the naive design shows immortal time bias while the honest ones do not.
"""

import math

import numpy as np
import pandas as pd
import pytest

from txlens_backend.survival.competing import aalen_johansen
from txlens_backend.survival.cox import COVARIATES, fit_cox
from txlens_backend.survival.extract import CENSORED, DEATH, GRAFT_LOSS
from txlens_backend.survival.km import Z95, expected_events, kaplan_meier, log_rank, risk_table
from txlens_backend.survival.landmark import (
    GROUPINGS,
    at_landmark,
    immortal_time_comparison,
    start_stop,
)
from txlens_backend.survival.simulate import TRUE_HAZARD_RATIOS, misclassify, simulate

TIME = pd.Series([1, 2, 2, 3, 4, 5], dtype=float)
EVENT = pd.Series([1, 1, 0, 1, 0, 1])


def test_kaplan_meier_matches_the_hand_calculation() -> None:
    km = kaplan_meier(TIME, EVENT)
    assert list(km.time) == [1, 2, 3, 4, 5]
    assert list(km.n_risk) == [6, 5, 3, 2, 1]
    assert np.allclose(km.estimate, [5 / 6, 4 / 6, 4 / 9, 4 / 9, 0])
    # R's std.err: Greenwood on the log scale, then a "log" interval capped at 1.
    se1 = math.sqrt(1 / (6 * 5))
    assert km.se[0] == pytest.approx(se1)
    assert km.lower[0] == pytest.approx(5 / 6 * math.exp(-Z95 * se1))
    assert km.upper[0] == pytest.approx(min(1, 5 / 6 * math.exp(Z95 * se1)))
    assert np.isnan(km.lower[-1])  # S = 0: the log interval is undefined (R gives NA)


def test_risk_table_counts_patients_still_followed() -> None:
    rows = risk_table(TIME, EVENT, [0, 2, 4])
    assert [(r.n_risk, r.cumulative_events, r.cumulative_censored) for r in rows] == [
        (6, 0, 0),
        (5, 2, 1),
        (2, 3, 2),
    ]


def test_log_rank_equals_the_hypergeometric_formula() -> None:
    time = pd.Series([3, 5, 7, 9, 11, 2, 4, 4, 6, 8], dtype=float)
    event = pd.Series([1, 1, 0, 1, 0, 1, 1, 1, 0, 1])
    group = pd.Series(["a"] * 5 + ["b"] * 5)
    o_minus_e = variance = 0.0
    for s in sorted(set(time[event == 1])):
        at_risk = time >= s
        n, n_a = at_risk.sum(), (at_risk & (group == "a")).sum()
        d = ((time == s) & (event == 1)).sum()
        o_minus_e += ((time == s) & (event == 1) & (group == "a")).sum() - d * n_a / n
        variance += d * (n_a / n) * (1 - n_a / n) * (n - d) / max(n - 1, 1)
    result = log_rank(time, event, group)
    assert result.chi2 == pytest.approx(o_minus_e**2 / variance)
    observed, expected = expected_events(time, event, group)
    assert sum(observed.values()) == pytest.approx(sum(expected.values()))


STATUS = pd.Series([GRAFT_LOSS, DEATH, CENSORED, GRAFT_LOSS, DEATH, GRAFT_LOSS, CENSORED, DEATH])
STATUS_TIME = pd.Series([2, 3, 3, 5, 5, 6, 8, 9], dtype=float)


def test_without_competing_deaths_cumulative_incidence_is_one_minus_km() -> None:
    cif = aalen_johansen(TIME, EVENT, GRAFT_LOSS)
    km = kaplan_meier(TIME, EVENT)
    assert np.allclose(cif.estimate, 1 - km.estimate)


def test_competing_deaths_make_one_minus_km_overstate_the_risk() -> None:
    cif = aalen_johansen(STATUS_TIME, STATUS, GRAFT_LOSS)
    km = kaplan_meier(STATUS_TIME, (STATUS == GRAFT_LOSS).astype(int))
    assert np.all(cif.estimate <= 1 - km.estimate + 1e-12)
    assert cif.estimate[-1] < 1 - km.estimate[-1]
    # Graft loss + death + still event-free = 1 at every time.
    death = aalen_johansen(STATUS_TIME, STATUS, DEATH)
    event_free = kaplan_meier(STATUS_TIME, (STATUS != CENSORED).astype(int))
    assert np.allclose(cif.estimate + death.estimate + event_free.estimate, 1)


def weighted_cif(time: np.ndarray, status: np.ndarray, weight: np.ndarray, at: float) -> float:
    """Aalen-Johansen with case weights, the slow way."""
    surv, cif = 1.0, 0.0
    for s in np.unique(time[time <= at]):
        n = weight[time >= s].sum()
        d_all, d_loss = (
            weight[(time == s) & (status != 0)].sum(),
            weight[(time == s) & (status == 1)].sum(),
        )
        cif += surv * d_loss / n
        surv *= 1 - d_all / n
    return cif


def test_jackknife_standard_error_is_the_sum_of_squared_influences() -> None:
    t, s = STATUS_TIME.to_numpy(), STATUS.to_numpy()
    cif = aalen_johansen(STATUS_TIME, STATUS, GRAFT_LOSS)
    h, influences = 1e-6, []
    for i in range(len(t)):
        w = np.ones(len(t))
        w[i] += h
        influences.append((weighted_cif(t, s, w, 6) - weighted_cif(t, s, np.ones(len(t)), 6)) / h)
    j = list(cif.time).index(6)
    assert cif.se[j] == pytest.approx(math.sqrt(sum(x**2 for x in influences)), rel=1e-4)


@pytest.fixture(scope="module")
def simulated() -> pd.DataFrame:
    return simulate()


def test_cox_recovers_the_true_hazard_ratios(simulated: pd.DataFrame) -> None:
    result = fit_cox(at_landmark(simulated, 30), list(COVARIATES))
    found = {t.key: t for t in result.terms}
    assert set(found) == set(COVARIATES) - {"heart"}  # kidney-only covariates leave no hearts
    assert any("same for every patient" in w for w in result.warnings)
    assert result.excluded == len(at_landmark(simulated, 30)) - result.n > 0
    for key, term in found.items():
        assert term.lower <= TRUE_HAZARD_RATIOS[key] <= term.upper, key
    everyone = fit_cox(simulated, ["age_per_decade", "deceased_donor", "heart"])
    heart = next(t for t in everyone.terms if t.key == "heart")
    assert heart.lower <= TRUE_HAZARD_RATIOS["heart"] <= heart.upper
    assert heart.n_exposed == int((simulated["organ"] == "heart").sum())


def test_immortal_time_bias_naive_vs_landmark_vs_time_varying() -> None:
    """dd-cfDNA >= 1% in year 1 has a true hazard ratio of 1 in the simulation."""
    naive, landmark, varying = immortal_time_comparison(simulate(n=20_000), "dd_cfdna_high_day")
    assert naive.hazard_ratio < 0.75 and naive.upper < 1  # looks protective: biased
    for honest in (landmark, varying):
        assert abs(math.log(honest.hazard_ratio)) < math.log(1.15), honest.design
    assert landmark.n < naive.n  # only patients still at risk at day 365


def test_landmark_keeps_patients_at_risk_and_restarts_the_clock() -> None:
    frame = pd.DataFrame({"time": [100, 365, 400, 900], "status": [1, 0, 1, 2]})
    kept = at_landmark(frame, 365)
    assert list(kept["time"]) == [35, 535]
    assert at_landmark(frame, 0).equals(frame)


def test_start_stop_rows_switch_exposure_on_at_the_first_high_result() -> None:
    frame = pd.DataFrame(
        {
            "time": [500.0, 200.0, 300.0, 80.0],
            "status": [GRAFT_LOSS, CENSORED, GRAFT_LOSS, GRAFT_LOSS],
            "day": [90.0, np.nan, 400.0, 80.0],
        }
    )
    rows = start_stop(frame, "day", 365)
    assert rows.values.tolist() == [
        [0, 0, 90, 0, 0],
        [0, 90, 500, 1, 1],  # exposed from day 90
        [1, 0, 200, 0, 0],  # never exposed
        [2, 0, 300, 1, 0],  # exposure after the window (and after the event)
        [3, 0, 80, 1, 0],  # exposure on the event day counts as after it
    ]


def test_groupings_wait_for_their_horizon(simulated: pd.DataFrame) -> None:
    assert GROUPINGS["dd_cfdna_year1"].horizon_days == 365
    assert GROUPINGS["dsa_month1"].horizon_days == 30
    labels = GROUPINGS["dsa_month1"].assign(simulated)
    assert labels[simulated["organ"] == "heart"].isna().all()  # no DSA test: not classifiable


def test_misclassification_follows_the_given_error_rates(simulated: pd.DataFrame) -> None:
    assert misclassify(simulated, "dgf", 1.0, 1.0)["dgf"].equals(simulated["dgf"])
    read = misclassify(simulated, "dgf", 0.8, 0.9)["dgf"]
    true = simulated["dgf"]
    assert read[true == 1].mean() == pytest.approx(0.8, abs=0.06)
    assert (1 - read[true == 0]).mean() == pytest.approx(0.9, abs=0.03)
    assert read[true.isna()].isna().all()
