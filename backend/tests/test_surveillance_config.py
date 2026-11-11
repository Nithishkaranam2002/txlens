"""Surveillance configs: the built-in YAMLs validate and bad configs are rejected."""

from datetime import date

import pytest
from pydantic import ValidationError
from surveillance_testutils import TEST_CONFIG

from txlens_backend.surveillance.config import Severity, load_configs, parse


def test_built_in_configs_validate() -> None:
    configs = load_configs()
    assert set(configs) == {"kidney_transplant", "heart_transplant", "oncology_tumor_dna"}
    assert {c.program for c in configs.values()} == {"kidney", "heart", "oncology"}


def test_organ_thresholds_come_from_config() -> None:
    configs = load_configs()
    kidney = configs["kidney_transplant"].rule("dd_cfdna_threshold")
    heart = configs["heart_transplant"].rule("dd_cfdna_threshold")
    assert kidney.kind == heart.kind == "threshold"
    assert [(lv.at_or_above, lv.severity) for lv in kidney.levels] == [
        (0.5, Severity.MEDIUM), (1.0, Severity.HIGH)
    ]  # fmt: skip
    assert [(lv.at_or_above, lv.severity) for lv in heart.levels] == [(0.2, Severity.HIGH)]


def test_schedule_due_dates_listed_months_then_every_interval() -> None:
    schedule = load_configs()["kidney_transplant"].schedule
    assert schedule is not None
    due = schedule.due_dates(date(2024, 1, 1), date(2025, 7, 2))
    assert [m for m, _ in due] == [1, 2, 3, 4, 6, 9, 12, 15, 18]
    assert due[0][1] == date(2024, 1, 31)  # round(30.4375) days


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            "marker: m\n    group: m\n    max_age_days",
            "marker: zz\n    group: m\n    max_age_days",
            "unknown markers",
        ),
        ("id: rising", "id: over", "unique"),
        ("kind: threshold", "kind: magic", "kind"),
        ("severity: medium, meaning: watch", "severity: urgent, meaning: watch", "severity"),
        ("title: Test program", "title: Test program\nsurprise: 1", "surprise"),
    ],
)
def test_bad_configs_fail_loudly(old: str, new: str, message: str) -> None:
    assert old in TEST_CONFIG
    text = TEST_CONFIG.replace(old, new, 1)
    with pytest.raises(ValidationError, match=message):
        parse(text)


def test_trend_rule_needs_a_criterion() -> None:
    text = TEST_CONFIG.replace("consecutive_increases: 2,\n     relative_rise_pct: 100,", "")
    with pytest.raises(ValidationError, match="consecutive_increases"):
        parse(text)


def test_schedule_rule_needs_a_schedule() -> None:
    text = TEST_CONFIG.replace(
        "schedule: {marker: m, months: [1, 2, 3], then_every_months: 3, tolerance_days: 14}", ""
    )
    with pytest.raises(ValidationError, match="needs a schedule"):
        parse(text)
