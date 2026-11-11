"""One small function per rule kind. Each looks only at data collected on or before `as_of` and
returns at most one flag, with a plain-English reason and the evidence it is based on.

These are deterministic checks that point a clinician at a patient. They never diagnose.
"""

import itertools
from collections.abc import Callable
from datetime import date, timedelta
from statistics import median
from typing import Any

from txlens_backend.surveillance.config import (
    DAYS_PER_MONTH,
    Detected,
    Marker,
    NewPositive,
    RangeByPhase,
    RelativeChange,
    RisingTrend,
    RuleBase,
    ScheduleAdherence,
    Severity,
    SurveillanceConfig,
    Threshold,
)
from txlens_backend.surveillance.model import Evidence, Flag, Observation, PatientSeries


def show(value: float, marker: Marker) -> str:
    """A value with its unit, e.g. "1.3%" or "2.1 mg/dL"."""
    number = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{number}{marker.unit}" if marker.unit == "%" else f"{number} {marker.unit}"


def result(marker: str, obs: Observation, role: str = "latest") -> Evidence:
    """Evidence pointing at one result."""
    return Evidence(marker, obs.day, obs.value, role)


def make_flag(
    config: SurveillanceConfig,
    rule: RuleBase,
    reason: str,
    evidence: list[Evidence],
    severity: Severity | None = None,
) -> Flag:
    """A flag stamped with the config that produced it."""
    return Flag(config.id, config.version, rule.id, rule.group_key, severity or rule.severity,
                reason, tuple(evidence))  # fmt: skip


def latest(
    rule: RuleBase, series: PatientSeries, marker: str, as_of: date
) -> tuple[tuple[Observation, ...], Observation | None]:
    """All results so far and the newest one, unless it is older than the rule's max age."""
    results = series.results(marker, as_of)
    if not results:
        return results, None
    newest = results[-1]
    if rule.max_age_days is not None and (as_of - newest.day).days > rule.max_age_days:
        return results, None
    return results, newest


def schedule_adherence(
    config: SurveillanceConfig, rule: ScheduleAdherence, series: PatientSeries, as_of: date
) -> Flag | None:
    """The most recent due test whose window has closed has no result in or after its window."""
    schedule = config.schedule
    assert schedule is not None  # guaranteed by SurveillanceConfig validation
    tolerance = timedelta(days=schedule.tolerance_days)
    closed = schedule.due_dates(series.anchor_date, as_of - tolerance - timedelta(days=1))
    if not closed:
        return None
    month, due = closed[-1]
    if any(o.day >= due - tolerance for o in series.results(schedule.marker, as_of)):
        return None
    label = config.markers[schedule.marker].label
    reason = (f"{label} test due around {due.isoformat()} (month {month}, "
              f"±{schedule.tolerance_days} days) has no result")  # fmt: skip
    return make_flag(config, rule, reason, [Evidence(schedule.marker, due, None, "due")])


def threshold(
    config: SurveillanceConfig, rule: Threshold, series: PatientSeries, as_of: date
) -> Flag | None:
    """The newest result reaches a threshold level; the highest level reached wins."""
    _, newest = latest(rule, series, rule.marker, as_of)
    if newest is None:
        return None
    reached = [lvl for lvl in rule.levels if newest.value >= lvl.at_or_above]
    if not reached:
        return None
    level = max(reached, key=lambda lvl: lvl.at_or_above)
    marker = config.markers[rule.marker]
    reason = (f"{marker.label} {show(newest.value, marker)} on {newest.day.isoformat()} is at "
              f"or above the {show(level.at_or_above, marker)} threshold "
              f"({level.meaning})")  # fmt: skip
    return make_flag(config, rule, reason, [result(rule.marker, newest)], level.severity)


def rising_trend(
    config: SurveillanceConfig, rule: RisingTrend, series: PatientSeries, as_of: date
) -> Flag | None:
    """The newest results rise in a row, or the newest jumps by a relative amount."""
    results, newest = latest(rule, series, rule.marker, as_of)
    if newest is None or newest.value < rule.min_latest or len(results) < 2:
        return None
    marker = config.markers[rule.marker]
    k = rule.consecutive_increases
    if k is not None and len(results) > k:
        run = results[-(k + 1) :]
        if all(a.value < b.value for a, b in itertools.pairwise(run)):
            values = " → ".join(show(o.value, marker) for o in run)
            reason = (f"{marker.label} rose {k} times in a row ({values}, "
                      f"latest {newest.day.isoformat()})")  # fmt: skip
            return make_flag(config, rule, reason, [result(rule.marker, o, "trend") for o in run])
    previous = results[-2]
    pct = rule.relative_rise_pct
    if pct is not None and previous.value > 0 and newest.value >= previous.value * (1 + pct / 100):
        rise = (newest.value / previous.value - 1) * 100
        reason = (f"{marker.label} rose {rise:.0f}% from {show(previous.value, marker)} "
                  f"({previous.day.isoformat()}) to {show(newest.value, marker)} "
                  f"({newest.day.isoformat()}), at least {pct:g}%")  # fmt: skip
        evidence = [result(rule.marker, previous, "previous"), result(rule.marker, newest)]
        return make_flag(config, rule, reason, evidence)
    return None


def relative_change(
    config: SurveillanceConfig, rule: RelativeChange, series: PatientSeries, as_of: date
) -> Flag | None:
    """The newest result moved by `pct` from the median of the patient's own recent results."""
    results, newest = latest(rule, series, rule.marker, as_of)
    if newest is None:
        return None
    first_day = series.anchor_date + timedelta(days=rule.start_after_days)
    if newest.day < first_day:
        return None
    window_start = max(newest.day - timedelta(days=rule.baseline_days), first_day)
    window_end = newest.day - timedelta(days=rule.exclude_recent_days)
    base = [o for o in results if window_start <= o.day < window_end and o != newest]
    if len(base) < rule.min_baseline_points:
        return None
    baseline = median(o.value for o in base)
    change = (newest.value - baseline) / baseline * 100 if baseline else 0.0
    moved = change if rule.direction == "up" else -change
    if moved < rule.pct:
        return None
    marker = config.markers[rule.marker]
    word = "rose" if rule.direction == "up" else "fell"
    reason = (f"{marker.label} {word} {moved:.0f}% to {show(newest.value, marker)} on "
              f"{newest.day.isoformat()} from the patient's own baseline "
              f"{show(baseline, marker)} (median of {len(base)} results), at least "
              f"{rule.pct:g}%")  # fmt: skip
    evidence = [result(rule.marker, o, "baseline") for o in base]
    return make_flag(config, rule, reason, [*evidence, result(rule.marker, newest)])


def range_by_phase(
    config: SurveillanceConfig, rule: RangeByPhase, series: PatientSeries, as_of: date
) -> Flag | None:
    """The newest result is outside the target range for its time since the anchor."""
    _, newest = latest(rule, series, rule.marker, as_of)
    if newest is None:
        return None
    months = (newest.day - series.anchor_date).days / DAYS_PER_MONTH
    phase = rule.phase_on(months)
    if phase is None or phase.low <= newest.value <= phase.high:
        return None
    marker = config.markers[rule.marker]
    side = "below" if newest.value < phase.low else "above"
    reason = (f"{marker.label} {show(newest.value, marker)} on {newest.day.isoformat()} is "
              f"{side} the target {show(phase.low, marker)} to {show(phase.high, marker)} "
              f"for month {months:.0f} after {config.anchor.replace('_', ' ')}")  # fmt: skip
    return make_flag(config, rule, reason, [result(rule.marker, newest)])


def new_positive(
    config: SurveillanceConfig, rule: NewPositive, series: PatientSeries, as_of: date
) -> Flag | None:
    """The first-ever positive result, for `active_days` after it was collected."""
    results = series.results(rule.marker, as_of)
    index = next((i for i, o in enumerate(results) if o.value >= rule.positive_at_or_above), None)
    if index is None:
        return None
    first = results[index]
    if (as_of - first.day).days > rule.active_days:
        return None
    marker = config.markers[rule.marker]
    evidence = [result(rule.marker, first, "first_positive")]
    before = ""
    if index > 0:
        prior = results[index - 1]
        evidence.insert(0, result(rule.marker, prior, "previous_negative"))
        before = f" after a negative {show(prior.value, marker)} on {prior.day.isoformat()}"
    reason = (f"New positive {marker.label} {show(first.value, marker)} on "
              f"{first.day.isoformat()}{before} (positive at or above "
              f"{show(rule.positive_at_or_above, marker)})")  # fmt: skip
    return make_flag(config, rule, reason, evidence)


def detected(
    config: SurveillanceConfig, rule: Detected, series: PatientSeries, as_of: date
) -> Flag | None:
    """The newest result is at or above the limit of detection."""
    _, newest = latest(rule, series, rule.marker, as_of)
    if newest is None or newest.value < rule.limit_of_detection:
        return None
    marker = config.markers[rule.marker]
    reason = (f"{marker.label} detected: {show(newest.value, marker)} on "
              f"{newest.day.isoformat()} (limit of detection "
              f"{show(rule.limit_of_detection, marker)})")  # fmt: skip
    return make_flag(config, rule, reason, [result(rule.marker, newest)])


CHECKS: dict[str, Callable[[SurveillanceConfig, Any, PatientSeries, date], Flag | None]] = {
    "schedule_adherence": schedule_adherence,
    "threshold": threshold,
    "rising_trend": rising_trend,
    "relative_change": relative_change,
    "range_by_phase": range_by_phase,
    "new_positive": new_positive,
    "detected": detected,
}
