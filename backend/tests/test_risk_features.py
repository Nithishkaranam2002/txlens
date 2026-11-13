"""Risk model visits, features and labels on hand-built records, and the no-leakage proof."""

import dataclasses
import math
import random
from datetime import date, timedelta

import pytest
from risk_testutils import SCHEDULE, day, obs, record

from txlens_backend.risk.dataset import biopsy_results
from txlens_backend.risk.features import FEATURE_NAMES, FEATURES, features_at
from txlens_backend.risk.labels import label, shortly_before_biopsy
from txlens_backend.risk.record import BiopsyResult, Finding, PatientRecord
from txlens_backend.risk.visits import is_scheduled_draw, scheduled_visits
from txlens_backend.surveillance.model import Observation

# --- visits -----------------------------------------------------------------------------


def test_one_visit_per_due_date_is_the_first_draw_in_its_window() -> None:
    r = record(observations={"dd_cfdna": obs((20, 0.1), (25, 0.2), (61, 0.3), (75, 0.4),
                                             (130, 0.5), (183, 0.6))})  # fmt: skip
    # Due: day 30, 61, 91, 183. Day 20 opens month 1 (16..44); 25 is a second draw in it;
    # 75 is inside month 2's window but after its first draw; 130 is in no window.
    assert scheduled_visits(r, SCHEDULE, day(400)) == [day(20), day(61), day(183)]


def test_no_visits_after_death_or_graft_loss() -> None:
    r = record(end_day=day(100))
    assert scheduled_visits(r, SCHEDULE, day(400)) == [day(30), day(61), day(91)]


def test_a_draw_is_scheduled_using_only_data_up_to_its_day() -> None:
    r = record()
    assert is_scheduled_draw(r, SCHEDULE, day(91))
    assert not is_scheduled_draw(r, SCHEDULE, day(92))


def test_a_later_biopsy_never_removes_a_visit() -> None:
    near_biopsy = record(biopsy_days=(day(190),))
    assert scheduled_visits(near_biopsy, SCHEDULE, day(400)) == scheduled_visits(
        record(biopsy_days=()), SCHEDULE, day(400)
    )


# --- features ---------------------------------------------------------------------------


def test_features_at_a_visit() -> None:
    f = features_at(record(), SCHEDULE, day(183))
    assert f["dd_cfdna_log"] == pytest.approx(math.log(1.2))
    assert f["dd_cfdna_slope"] == pytest.approx((1.2 - 0.6) / (92 / 30.4375))
    assert f["dd_cfdna_vs_min"] == pytest.approx(1.2 / 0.2)
    assert f["creatinine_change_pct"] == pytest.approx(50.0)  # 1.65 vs median(1.0, 1.2, 1.1)
    troughs = [8.0, 10.0, 6.0, 8.0]  # all within the 180 days before day 183
    mean = sum(troughs) / 4
    sd = math.sqrt(sum((t - mean) ** 2 for t in troughs) / 3)
    assert f["tacrolimus_cv"] == pytest.approx(100 * sd / mean)
    assert f["dsa_positive"] == 1.0
    assert f["months_since_transplant"] == pytest.approx(183 / 30.4375)
    assert f["prior_rejection"] == 0.0
    assert (f["organ_heart"], f["donor_deceased"], f["dgf"]) == (0.0, 0.0, 0.0)
    assert list(f) == list(FEATURE_NAMES) == [x.name for x in FEATURES]


def test_first_visit_has_no_trend_and_no_baseline() -> None:
    f = features_at(record(), SCHEDULE, day(30))
    assert math.isnan(f["dd_cfdna_slope"]) and math.isnan(f["dd_cfdna_vs_min"])
    assert math.isnan(f["creatinine_change_pct"])  # no creatinine yet
    assert math.isnan(f["tacrolimus_cv"])  # fewer than 3 troughs
    assert f["dsa_positive"] == 0.0


def test_prior_rejection_needs_a_result_recorded_before_the_visit() -> None:
    known_later = record(results=(BiopsyResult(day(170), day(183), Finding.REJECTION),))
    assert features_at(known_later, SCHEDULE, day(183))["prior_rejection"] == 0.0
    assert features_at(known_later, SCHEDULE, day(274))["prior_rejection"] == 1.0
    borderline = record(results=(BiopsyResult(day(100), day(101), Finding.BORDERLINE),))
    assert features_at(borderline, SCHEDULE, day(183))["prior_rejection"] == 0.0


def test_dgf_counts_once_recorded_and_only_for_kidneys() -> None:
    assert features_at(record(dgf_known_day=day(61)), SCHEDULE, day(30))["dgf"] == 0.0
    assert features_at(record(dgf_known_day=day(61)), SCHEDULE, day(61))["dgf"] == 1.0
    heart = record(organ="heart", donor_type=None, dgf_known_day=day(10))
    f = features_at(heart, SCHEDULE, day(61))
    assert (f["dgf"], f["organ_heart"], f["donor_deceased"]) == (0.0, 1.0, 1.0)


# --- no leakage -------------------------------------------------------------------------


def perturb_after(r: PatientRecord, visit: date, rng: random.Random) -> PatientRecord:
    """The same record with everything dated after `visit` changed, dropped or added."""

    def future(series: tuple[Observation, ...]) -> tuple[Observation, ...]:
        kept = [o for o in series if o.day <= visit]
        later = [Observation(o.day, o.value * rng.uniform(0.1, 10)) for o in series
                 if o.day > visit and rng.random() < 0.7]  # fmt: skip
        extra = [Observation(visit + timedelta(days=rng.randint(1, 300)), rng.uniform(0, 3000))
                 for _ in range(rng.randint(0, 4))]  # fmt: skip
        return tuple(kept + sorted(later + extra, key=lambda o: o.day))

    results = tuple(x for x in r.results if x.known_day < visit) + tuple(
        BiopsyResult(visit + timedelta(days=d), visit + timedelta(days=d + 3), Finding.REJECTION)
        for d in rng.sample(range(1, 200), rng.randint(0, 3))
    )
    return dataclasses.replace(
        r,
        observations={k: future(v) for k, v in r.observations.items()},
        biopsy_days=(*(b for b in r.biopsy_days if b <= visit), visit + timedelta(days=5)),
        results=results,
        dgf_known_day=r.dgf_known_day
        if (r.dgf_known_day or date.max) <= visit
        else visit + timedelta(days=rng.randint(1, 100)),
        follow_up_end=visit + timedelta(days=rng.randint(0, 900)),
        end_day=visit + timedelta(days=rng.randint(1, 900)) if rng.random() < 0.5 else None,
    )


def same(a: dict[str, float], b: dict[str, float]) -> bool:
    return all((math.isnan(a[k]) and math.isnan(b[k])) or a[k] == b[k] for k in a)


@pytest.mark.parametrize("seed", range(25))
def test_no_feature_uses_data_dated_after_the_visit(seed: int) -> None:
    """Changing, deleting or adding anything after the visit leaves every feature unchanged."""
    rng = random.Random(seed)
    r = record(dgf_known_day=day(120))
    for visit in scheduled_visits(r, SCHEDULE, day(730)):
        before = features_at(r, SCHEDULE, visit)
        assert same(before, features_at(perturb_after(r, visit, rng), SCHEDULE, visit)), visit


# --- labels -----------------------------------------------------------------------------


def test_label_window_is_the_90_days_after_the_visit() -> None:
    r = record()  # rejection biopsy on day 200
    assert label(r, day(109)) == 0  # window ends day 199
    assert label(r, day(110)) == 1  # day 200 is the last day of the window
    assert label(r, day(199)) == 1
    assert label(r, day(200)) == 0  # a biopsy on the visit day is not in the future window


def test_label_is_missing_when_the_window_is_not_fully_observed() -> None:
    assert label(record(follow_up_end=day(250)), day(183)) == 1  # positive inside follow-up
    assert label(record(follow_up_end=day(300)), day(274)) is None
    assert label(record(end_day=day(320)), day(274)) is None  # death before the window ends


def test_borderline_is_negative_except_in_the_sensitivity_label() -> None:
    r = record(results=(BiopsyResult(day(200), day(205), Finding.BORDERLINE),))
    assert label(r, day(183)) == 0
    assert label(r, day(183), borderline=True) == 1


def test_shortly_before_biopsy() -> None:
    assert shortly_before_biopsy(record(), day(186))
    assert not shortly_before_biopsy(record(), day(183))


# --- biopsy results from OMOP records ---------------------------------------------------


def test_results_are_tied_to_the_biopsy_they_follow() -> None:
    biopsies = [day(100), day(200)]
    results = biopsy_results(biopsies, [
        (day(209), False),  # note 9 days after the day-200 biopsy
        (day(200), False),  # pathology report, dated on the biopsy
        (day(150), False),  # 50 days after a biopsy: not biopsy-proven
        (day(103), True),  # borderline note after the day-100 biopsy
    ])  # fmt: skip
    assert results == (
        BiopsyResult(day(100), day(103), Finding.BORDERLINE),
        BiopsyResult(day(200), day(200), Finding.REJECTION),
    )


def test_rejection_wins_over_borderline_for_one_biopsy() -> None:
    results = biopsy_results([day(100)], [(day(100), True), (day(104), False)])
    assert results == (BiopsyResult(day(100), day(104), Finding.REJECTION),)
