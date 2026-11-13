"""The outcome: a biopsy-proven rejection with its biopsy in the 90 days after a visit.

The window is (visit, visit + 90 days]. A visit whose window is not fully observed (follow-up
ends, or the patient dies or loses the graft, before a rejection biopsy in the window) has no
label and is left out of training and evaluation.
"""

from datetime import date, timedelta

from txlens_backend.risk.record import Finding, PatientRecord

HORIZON = timedelta(days=90)
# A pre-biopsy draw within this many days of a biopsy (sensitivity analysis (a) only: it uses
# the future biopsy, so the model never sees it).
PRE_BIOPSY = timedelta(days=14)


def label(record: PatientRecord, visit: date, *, borderline: bool = False) -> int | None:
    """1 if a rejection biopsy falls in the window, 0 if the window is fully observed without
    one, None if it is not. `borderline=True` also counts borderline results as positive."""
    positive = {Finding.REJECTION, Finding.BORDERLINE} if borderline else {Finding.REJECTION}
    end = visit + HORIZON
    if any(visit < r.biopsy_day <= end and r.finding in positive for r in record.results):
        return 1
    if record.follow_up_end < end:
        return None
    if record.end_day is not None and record.end_day <= end:
        return None
    return 0


def next_rejection(record: PatientRecord, visit: date) -> date | None:
    """The first rejection biopsy in the window (evaluation only: groups visits by episode)."""
    end = visit + HORIZON
    days = [r.biopsy_day for r in record.results
            if r.finding is Finding.REJECTION and visit < r.biopsy_day <= end]  # fmt: skip
    return min(days, default=None)


def shortly_before_biopsy(record: PatientRecord, visit: date) -> bool:
    """A biopsy follows within 14 days (for sensitivity analysis (a); uses the future)."""
    return any(visit <= b <= visit + PRE_BIOPSY for b in record.biopsy_days)
