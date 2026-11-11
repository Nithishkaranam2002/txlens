"""The engine: run a config's rules over one patient as of a date, and score the result.

Pure Python with no database access, so the nightly job, the API and the evaluation all use
exactly the same logic.
"""

from collections.abc import Iterable, Iterator
from datetime import date, timedelta

from txlens_backend.surveillance.config import NewPositive, Severity, SurveillanceConfig
from txlens_backend.surveillance.model import Flag, PatientSeries
from txlens_backend.surveillance.rules import CHECKS


def evaluate(config: SurveillanceConfig, series: PatientSeries, as_of: date) -> list[Flag]:
    """Flags for one patient on `as_of`. Nothing before the anchor or after surveillance ends."""
    if series.program != config.program or as_of < series.anchor_date:
        return []
    if series.end_date is not None and as_of >= series.end_date:
        return []
    flags = (CHECKS[rule.kind](config, rule, series, as_of) for rule in config.rules)
    return [flag for flag in flags if flag is not None]


def priority(flags: Iterable[tuple[Severity, str]], weights: dict[Severity, float]) -> float:
    """Sum of severity weights, counting each rule group once at its highest severity.

    Takes (severity, group) pairs so stored flags and fresh flags score the same way.
    """
    best: dict[str, float] = {}
    for severity, group in flags:
        best[group] = max(best.get(group, 0.0), weights[severity])
    return sum(best.values())


def change_days(config: SurveillanceConfig, series: PatientSeries, until: date) -> list[date]:
    """Every day up to `until` on which this patient's flags can change.

    Flags change only when a result arrives, when a result ages out of a rule's window, when a
    due test's tolerance window closes, at the anchor and at the end of surveillance. Between
    these days `evaluate` returns the same answer, so a replay only needs to visit these days.
    """
    windows = {r.max_age_days for r in config.rules if r.max_age_days is not None}
    windows |= {r.active_days for r in config.rules if isinstance(r, NewPositive)}
    days = {series.anchor_date}
    for marker in config.markers:
        for obs in series.observations.get(marker, ()):
            days.add(obs.day)
            days.update(obs.day + timedelta(days=w + 1) for w in windows)
    if series.end_date is not None:
        days.add(series.end_date)
    schedule = config.schedule
    if schedule is not None:
        close = timedelta(days=schedule.tolerance_days + 1)
        days.update(due + close for _, due in schedule.due_dates(series.anchor_date, until))
    return sorted(d for d in days if d <= until)


def replay(
    config: SurveillanceConfig, series: PatientSeries, start: date, end: date
) -> Iterator[tuple[date, list[Flag]]]:
    """The patient's flags on `start` and on every later day they may change, up to `end`.

    The flags returned for a day hold until the next day yielded.
    """
    yield start, evaluate(config, series, start)
    for day in change_days(config, series, end):
        if day > start:
            yield day, evaluate(config, series, day)
