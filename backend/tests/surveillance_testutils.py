"""Hand-built patient series and a tiny config for surveillance rule tests."""

from datetime import date, timedelta

from txlens_backend.surveillance.config import SurveillanceConfig, parse
from txlens_backend.surveillance.model import Observation, PatientSeries

ANCHOR = date(2024, 1, 1)

TEST_CONFIG = """
id: test_program
version: 3
title: Test program
program: test
anchor: transplant_date
markers:
  m: {label: Marker, vocabulary: LOINC, code: "1-1", unit: "%"}
  c: {label: Creatinine, vocabulary: LOINC, code: "2-2", unit: mg/dL}
  t: {label: Drug level, vocabulary: LOINC, code: "3-3", unit: ng/mL}
  d: {label: DSA, vocabulary: X, code: DSA, unit: MFI, source_concept: true}
schedule: {marker: m, months: [1, 2, 3], then_every_months: 3, tolerance_days: 14}
rules:
  - {id: overdue, kind: schedule_adherence, severity: low}
  - id: over
    kind: threshold
    marker: m
    group: m
    max_age_days: 90
    levels:
      - {at_or_above: 0.5, severity: medium, meaning: watch}
      - {at_or_above: 1.0, severity: high, meaning: high risk}
  - {id: rising, kind: rising_trend, marker: m, group: m, consecutive_increases: 2,
     relative_rise_pct: 100, min_latest: 0.3}
  - {id: cr_up, kind: relative_change, marker: c, group: function, direction: up, pct: 25,
     baseline_days: 90, min_baseline_points: 2, start_after_days: 30}
  - id: level
    kind: range_by_phase
    marker: t
    severity: low
    phases:
      - {from_month: 0, to_month: 3, low: 8, high: 12}
      - {from_month: 3, low: 5, high: 8}
  - {id: dsa, kind: new_positive, marker: d, severity: high, positive_at_or_above: 1000,
     active_days: 90}
"""


def config() -> SurveillanceConfig:
    """The test config."""
    return parse(TEST_CONFIG)


def series(end_date: date | None = None, /, **markers: list[tuple[int, float]]) -> PatientSeries:
    """A patient whose results are given as (days after anchor, value) per marker."""
    return PatientSeries(
        patient_key="p1",
        program="test",
        anchor_date=ANCHOR,
        end_date=end_date,
        observations={
            m: tuple(Observation(ANCHOR + timedelta(days=d), v) for d, v in sorted(values))
            for m, values in markers.items()
        },
    )


def day(n: int) -> date:
    """`n` days after the anchor."""
    return ANCHOR + timedelta(days=n)
