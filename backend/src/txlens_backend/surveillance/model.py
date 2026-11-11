"""What the engine reads (a patient's marker series) and what it produces (flags)."""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date

from txlens_backend.surveillance.config import Severity

# Values are rounded before hashing: unit conversions in the pipeline produce float noise.
EVIDENCE_DECIMALS = 3


@dataclass(frozen=True)
class Observation:
    """One result of one marker."""

    day: date
    value: float


@dataclass(frozen=True)
class PatientSeries:
    """Everything the engine knows about one patient in one program.

    `anchor_date` is the program's start (transplant date, end of cancer treatment).
    `end_date` (death, graft loss) ends surveillance. Observations are sorted by day.
    """

    patient_key: str
    program: str
    anchor_date: date
    end_date: date | None
    observations: Mapping[str, tuple[Observation, ...]] = field(default_factory=dict)

    def results(self, marker: str, as_of: date) -> tuple[Observation, ...]:
        """Results of one marker collected on or before `as_of`, oldest first."""
        return tuple(o for o in self.observations.get(marker, ()) if o.day <= as_of)


@dataclass(frozen=True)
class Evidence:
    """A fact a flag points to: a result (with value) or a due date (without)."""

    marker: str
    day: date
    value: float | None
    role: str

    def as_dict(self) -> dict[str, object]:
        """JSON-ready form."""
        return {"marker": self.marker, "date": self.day.isoformat(), "value": self.value,
                "role": self.role}  # fmt: skip


@dataclass(frozen=True)
class Flag:
    """A reason to review a patient. Not a diagnosis."""

    config_id: str
    config_version: int
    rule_id: str
    group: str
    severity: Severity
    reason: str
    evidence: tuple[Evidence, ...]

    @property
    def evidence_key(self) -> str:
        """Identity of "this rule on this evidence". The config version is left out on purpose:
        a new config version must not reopen a flag already resolved for the same results."""
        parts = [self.rule_id] + [
            f"{e.marker}|{e.day.isoformat()}|"
            + ("" if e.value is None else f"{e.value:.{EVIDENCE_DECIMALS}f}")
            + f"|{e.role}"
            for e in self.evidence
        ]
        return hashlib.sha256("\n".join(parts).encode()).hexdigest()
