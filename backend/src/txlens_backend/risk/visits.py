"""Which dd-cfDNA draws are surveillance visits (the model's prediction points).

The draw reason (scheduled or for-cause) never reaches OMOP, so a visit is defined by the
schedule alone: the first dd-cfDNA draw within the tolerance window of a due date, one per due
date. Both facts are known on the draw day, so the nightly job can apply the same rule.

A later biopsy is never used to drop a draw: that is future information, and it would remove
the visits just before a rejection. How many kept visits were really for-cause draws is
measured in the evaluation against the generator's ground truth.
"""

from datetime import date, timedelta

from txlens_backend.risk.record import PatientRecord
from txlens_backend.surveillance.config import Schedule

DD_CFDNA = "dd_cfdna"


def scheduled_visits(record: PatientRecord, schedule: Schedule, until: date) -> list[date]:
    """Visit days up to `until`, oldest first; none on or after death or graft loss."""
    tolerance = timedelta(days=schedule.tolerance_days)
    due_dates = schedule.due_dates(record.transplant_day, until + tolerance)
    draws = [o.day for o in record.values(schedule.marker, until)]
    visits: list[date] = []
    for _, due in due_dates:
        first = next((d for d in draws if due - tolerance <= d <= due + tolerance), None)
        if first is None or (visits and first <= visits[-1]):
            continue
        if record.end_day is not None and first >= record.end_day:
            break
        visits.append(first)
    return visits


def is_scheduled_draw(record: PatientRecord, schedule: Schedule, day: date) -> bool:
    """Whether the dd-cfDNA draw on `day` is a surveillance visit (uses data up to `day` only)."""
    return day in scheduled_visits(record, schedule, day)
