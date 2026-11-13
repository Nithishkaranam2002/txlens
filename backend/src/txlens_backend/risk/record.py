"""Everything the risk model may know about one transplant recipient, with the date each fact
became known.

Features read a record only up to the prediction day; labels read what happened afterwards.
Keeping both in one dated structure lets a test prove that features never look ahead.
"""

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from txlens_backend.surveillance.model import Observation


class Finding(StrEnum):
    """A biopsy result the model cares about."""

    REJECTION = "rejection"  # TCMR, ABMR or mixed (the label)
    BORDERLINE = "borderline"  # negative in the main analysis, positive in a sensitivity one


@dataclass(frozen=True)
class BiopsyResult:
    """A rejection finding tied to the biopsy it came from.

    `biopsy_day` is when the tissue was taken (the label's event day). `known_day` is when the
    result was first recorded in OMOP (pathology report or clinic note), which is what a
    prediction on a later day may use.
    """

    biopsy_day: date
    known_day: date
    finding: Finding


@dataclass(frozen=True)
class PatientRecord:
    """One recipient's dated history, as loaded from OMOP (or built by hand in tests)."""

    key: str
    tenant: str
    organ: str
    donor_type: str | None
    sex: str | None
    birth_year: int | None
    transplant_day: date
    follow_up_end: date
    end_day: date | None  # death or graft loss: no visits or labels after it
    observations: dict[str, tuple[Observation, ...]] = field(default_factory=dict)
    biopsy_days: tuple[date, ...] = ()
    results: tuple[BiopsyResult, ...] = ()
    dgf_known_day: date | None = None

    def values(self, marker: str, as_of: date) -> tuple[Observation, ...]:
        """Results of one marker dated on or before `as_of`, oldest first."""
        return tuple(o for o in self.observations.get(marker, ()) if o.day <= as_of)
