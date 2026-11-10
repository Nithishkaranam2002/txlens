from datetime import timedelta

from txlens_datagen.config import SurveillanceConfig
from txlens_datagen.models import PatientRecord
from txlens_datagen.rand import rng_for
from txlens_datagen.schedule import draw_schedule, due_months, month_to_day

THREE_YEARS = month_to_day(36)


def test_year_one_months_then_quarterly() -> None:
    months = due_months(THREE_YEARS, SurveillanceConfig())
    assert months[:7] == [1, 2, 3, 4, 6, 9, 12]
    assert months[7:] == list(range(15, 37, 3))


def test_schedule_stops_at_last_day() -> None:
    assert due_months(100, SurveillanceConfig()) == [1, 2, 3]
    assert due_months(20, SurveillanceConfig()) == []


def test_draw_days_jitter_and_missed_rate() -> None:
    config = SurveillanceConfig()
    tests = [t for i in range(500) for t in draw_schedule(THREE_YEARS, config, rng_for(1, i))]
    for t in tests:
        assert abs(t.day - month_to_day(t.month)) <= config.jitter_days
    missed = sum(t.missed for t in tests) / len(tests)
    assert 0.05 <= missed <= 0.10


def test_schedule_ends_with_follow_up(cohort: list[PatientRecord]) -> None:
    for record in cohort:
        for test in record.schedule:
            assert test.due_date <= record.outcome.last_follow_up_date


def test_done_tests_have_a_dd_cfdna_result_that_day(cohort: list[PatientRecord]) -> None:
    for record in cohort[:300]:
        labs = {lab.lab_id: lab for lab in record.labs}
        for test in record.schedule:
            if test.done:
                lab = labs[test.lab_id]
                assert lab.lab == "dd_cfdna"
                assert lab.collected_at.date() == test.due_date
            else:
                assert test.lab_id == ""


def test_missed_tests_have_no_surveillance_result(cohort: list[PatientRecord]) -> None:
    for record in cohort[:300]:
        surveillance_days = {
            lab.collected_at.date() for lab in record.labs if lab.reason == "surveillance"
        }
        for test in record.schedule:
            if not test.done:
                assert test.due_date not in surveillance_days
                assert test.due_date - record.patient.transplant_date >= timedelta(days=25)
