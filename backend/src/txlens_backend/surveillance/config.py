"""Surveillance configs: which marker, schedule, thresholds and trend rules apply to a program.

A config is a versioned YAML file. Nothing organ- or disease-specific lives in code: the kidney,
heart and oncology programs differ only in their YAML. Every flag records the config id and
version that produced it, so a flag can always be traced back to the exact rule text.
"""

from datetime import date, timedelta
from enum import StrEnum
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

# Same month length the generator and clinicians' "month N" convention use.
DAYS_PER_MONTH = 30.4375


class Severity(StrEnum):
    """How urgently a flag should be reviewed. It is never a diagnosis."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


DEFAULT_WEIGHTS = {Severity.HIGH: 10.0, Severity.MEDIUM: 4.0, Severity.LOW: 1.0}


class Frozen(BaseModel):
    """Config models are immutable and reject unknown keys (typos fail loudly)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Marker(Frozen):
    """A measurement, found by vocabulary code. `source_concept` markers (local codes with no
    standard concept) are matched on `measurement_source_concept_id`."""

    label: str
    vocabulary: str
    code: str
    unit: str
    source_concept: bool = False


class Schedule(Frozen):
    """When the marker test is due, in months after the anchor date (e.g. transplant)."""

    marker: str
    months: tuple[int, ...]
    then_every_months: int = Field(gt=0)
    tolerance_days: int = Field(ge=0)

    def due_dates(self, anchor: date, until: date) -> list[tuple[int, date]]:
        """(month, due date) from the anchor up to and including `until`."""
        due_dates: list[tuple[int, date]] = []
        month = 0
        listed = iter(sorted(self.months))
        while True:
            month = next(listed, month + self.then_every_months)
            due = anchor + timedelta(days=round(month * DAYS_PER_MONTH))
            if due > until:
                return due_dates
            due_dates.append((month, due))


class RuleBase(Frozen):
    """Fields every rule has. `group` joins rules that measure the same thing (creatinine and
    eGFR both measure graft function), so the priority score counts the group once."""

    id: str
    severity: Severity = Severity.MEDIUM
    group: str | None = None
    # Latest-value rules stop firing once their newest result is older than this.
    max_age_days: int | None = Field(default=None, gt=0)

    @property
    def group_key(self) -> str:
        """The group the priority score counts this rule under."""
        return self.group or self.id


class ScheduleAdherence(RuleBase):
    """The scheduled marker test has no result within the tolerance window around its due date."""

    kind: Literal["schedule_adherence"]


class Level(Frozen):
    """One threshold level, e.g. >= 1.0% is "high risk of rejection"."""

    at_or_above: float
    severity: Severity
    meaning: str


class Threshold(RuleBase):
    """The latest result is at or above a threshold level (highest matching level wins)."""

    kind: Literal["threshold"]
    marker: str
    levels: tuple[Level, ...] = Field(min_length=1)


class RisingTrend(RuleBase):
    """The latest results keep rising: `consecutive_increases` rises in a row, or the latest is
    `relative_rise_pct` above the previous one. `min_latest` ignores rises in the noise floor."""

    kind: Literal["rising_trend"]
    marker: str
    consecutive_increases: int | None = Field(default=None, ge=1)
    relative_rise_pct: float | None = Field(default=None, gt=0)
    min_latest: float = 0.0

    @model_validator(mode="after")
    def needs_a_criterion(self) -> "RisingTrend":
        """At least one of the two trend tests must be set."""
        if self.consecutive_increases is None and self.relative_rise_pct is None:
            raise ValueError("set consecutive_increases and/or relative_rise_pct")
        return self


class RelativeChange(RuleBase):
    """The latest result moved `pct` percent up or down from the patient's own baseline: the
    median of results in the `baseline_days` before it (the last `exclude_recent_days` left out)."""

    kind: Literal["relative_change"]
    marker: str
    direction: Literal["up", "down"]
    pct: float = Field(gt=0)
    baseline_days: int = Field(gt=0)
    exclude_recent_days: int = Field(default=0, ge=0)
    min_baseline_points: int = Field(default=2, ge=1)
    start_after_days: int = Field(default=0, ge=0)


class Phase(Frozen):
    """A target range that applies from `from_month` (inclusive) to `to_month` (exclusive)."""

    from_month: int = Field(ge=0)
    to_month: int | None = None
    low: float
    high: float


class RangeByPhase(RuleBase):
    """The latest result is outside the target range for the time since the anchor date."""

    kind: Literal["range_by_phase"]
    marker: str
    phases: tuple[Phase, ...] = Field(min_length=1)

    def phase_on(self, months: float) -> Phase | None:
        """The phase that covers this many months after the anchor."""
        for phase in self.phases:
            if phase.from_month <= months and (phase.to_month is None or months < phase.to_month):
                return phase
        return None


class NewPositive(RuleBase):
    """The first result at or above `positive_at_or_above` (no earlier positive), shown for
    `active_days` after it was collected."""

    kind: Literal["new_positive"]
    marker: str
    positive_at_or_above: float
    active_days: int = Field(gt=0)


class Detected(RuleBase):
    """The latest result is at or above the assay's limit of detection."""

    kind: Literal["detected"]
    marker: str
    limit_of_detection: float


Rule = Annotated[
    ScheduleAdherence | Threshold | RisingTrend | RelativeChange | RangeByPhase | NewPositive
    | Detected,
    Field(discriminator="kind"),
]  # fmt: skip


class SurveillanceConfig(Frozen):
    """One program's surveillance rules, e.g. kidney transplant dd-cfDNA monitoring."""

    id: str
    version: int = Field(ge=1)
    title: str
    program: str
    anchor: str
    markers: dict[str, Marker]
    schedule: Schedule | None = None
    rules: tuple[Rule, ...] = Field(min_length=1)
    severity_weights: dict[Severity, float] = DEFAULT_WEIGHTS

    @model_validator(mode="after")
    def references_resolve(self) -> "SurveillanceConfig":
        """Every rule names a known marker, rule ids are unique, a schedule rule has a schedule."""
        ids = [rule.id for rule in self.rules]
        if len(set(ids)) != len(ids):
            raise ValueError("rule ids must be unique")
        used = [getattr(rule, "marker", None) for rule in self.rules]
        if self.schedule is not None:
            used.append(self.schedule.marker)
        unknown = {m for m in used if m is not None and m not in self.markers}
        if unknown:
            raise ValueError(f"unknown markers: {sorted(unknown)}")
        if self.schedule is None and any(r.kind == "schedule_adherence" for r in self.rules):
            raise ValueError("a schedule_adherence rule needs a schedule")
        return self

    def rule(self, rule_id: str) -> Rule:
        """The rule with this id."""
        return next(rule for rule in self.rules if rule.id == rule_id)


def parse(text: str) -> SurveillanceConfig:
    """Validate one YAML document."""
    return SurveillanceConfig.model_validate(yaml.safe_load(text))


def load_file(path: Path) -> SurveillanceConfig:
    """Validate one YAML file."""
    return parse(path.read_text())


@cache
def built_in_configs() -> tuple[SurveillanceConfig, ...]:
    """The configs shipped with the package, parsed once per process (they are package files
    and frozen models; parsing them on every worklist request took 40% of its time)."""
    folder = resources.files("txlens_backend.surveillance") / "configs"
    return tuple(parse(f.read_text()) for f in folder.iterdir() if f.name.endswith(".yaml"))


def load_configs() -> dict[str, SurveillanceConfig]:
    """Every built-in config, keyed by id. Fails at startup if any config is invalid."""
    configs = built_in_configs()
    by_id = {c.id: c for c in configs}
    if len(by_id) != len(configs):
        raise ValueError("config ids must be unique")
    return by_id
