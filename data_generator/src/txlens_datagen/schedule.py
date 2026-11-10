"""dd-cfDNA surveillance schedule: which months a test is due and which tests were missed."""

from dataclasses import dataclass

from txlens_datagen.config import SurveillanceConfig
from txlens_datagen.rand import Rng, chance, randint

DAYS_PER_MONTH = 30.4375


@dataclass(frozen=True)
class DueTest:
    """One surveillance test: month due, actual draw day (post-transplant), and missed flag."""

    month: int
    day: int
    missed: bool


def month_to_day(month: int) -> int:
    """Convert months after transplant to days after transplant."""
    return round(month * DAYS_PER_MONTH)


def due_months(last_day: int, config: SurveillanceConfig) -> list[int]:
    """Months at which a test is due, up to `last_day` days after transplant.

    Year 1 uses the listed months; after that a test is due every `interval_after_months`.
    """
    months = [m for m in config.months_year1 if month_to_day(m) <= last_day]
    month = max(config.months_year1) + config.interval_after_months
    while month_to_day(month) <= last_day:
        months.append(month)
        month += config.interval_after_months
    return months


def draw_schedule(last_day: int, config: SurveillanceConfig, rng: Rng) -> list[DueTest]:
    """Due tests up to `last_day`, each with a small date jitter and a chance of being missed."""
    tests = []
    for month in due_months(last_day, config):
        day = month_to_day(month) + randint(rng, -config.jitter_days, config.jitter_days)
        tests.append(DueTest(month, min(day, last_day), chance(rng, config.missed_rate)))
    return tests
