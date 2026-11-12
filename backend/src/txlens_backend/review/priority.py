"""Queue order: rejection-related and high-severity items first, then the QC sample, then the
rest; oldest first within a tier.

Rejection comes first because a wrong answer there changes care (a missed current rejection)
or the research record (a past rejection counted as current). The QC sample comes next: it is
the only unbiased measure of the automatic path's error rate, so it should not wait behind
the bulk of low-confidence items.
"""

from dataclasses import dataclass
from datetime import datetime

REJECTION_DIAGNOSES = frozenset({"TCMR", "ABMR", "mixed", "borderline"})
# Grades that make a rejection severe: Banff II and above (kidney), ISHLT 2R/3R (heart).
HIGH_GRADES = frozenset({"IIA", "IIB", "III", "2R", "3R"})
REJECTION, QC_SAMPLE, OTHER = 0, 1, 2
TIER_LABELS = {REJECTION: "rejection / high severity", QC_SAMPLE: "QC sample", OTHER: "other"}


@dataclass(frozen=True)
class Signals:
    """What an item says about its urgency."""

    queued_at: datetime
    rejection_related: bool = False
    high_severity: bool = False
    qc_sample: bool = False


def tier(signals: Signals) -> int:
    """0: rejection or high severity; 1: QC sample; 2: everything else."""
    if signals.rejection_related or signals.high_severity:
        return REJECTION
    return QC_SAMPLE if signals.qc_sample else OTHER


def sort_key(signals: Signals) -> tuple[int, datetime]:
    """Tier first, then the oldest item."""
    return tier(signals), signals.queued_at


def biopsy_signals(
    queued_at: datetime, diagnosis: str | None, grade: str | None, qc_sample: bool
) -> Signals:
    """A biopsy document: rejection if the extracted diagnosis is one; severe by grade."""
    return Signals(
        queued_at=queued_at,
        rejection_related=diagnosis in REJECTION_DIAGNOSES,
        high_severity=diagnosis in ("ABMR", "mixed") or grade in HIGH_GRADES,
        qc_sample=qc_sample,
    )
