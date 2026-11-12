"""Review queue metrics for one center: what waits, how fast people review, how often the
model was right.

Agreement is reported twice. Items in the QC sample were drawn at random from those the
model would have auto-approved, so their agreement estimates the automatic path's accuracy
without bias. Every other item came to review because something looked wrong (low confidence,
a rule), so its agreement is lower by design and says how useful review is, not how good the
model is.
"""

from collections import Counter
from datetime import date

from pydantic import BaseModel
from sqlalchemy import Connection, text

from txlens_backend.review.queue import pending
from txlens_backend.review.reasons import ITEM_TYPES

DAYS = 30
PER_DAY_SQL = text(
    f"""
    SELECT decided_at::date AS day, item_type, count(*) AS n FROM app.review_decision
    WHERE decided_at >= now() - interval '{DAYS} days'
    GROUP BY 1, 2 ORDER BY 1, 2
    """
)
MEDIAN_SQL = text(
    """
    SELECT item_type, count(*) AS n,
           percentile_cont(0.5) WITHIN GROUP (
               ORDER BY extract(epoch FROM decided_at - queued_at) / 3600) AS hours
    FROM app.review_decision GROUP BY item_type ORDER BY item_type
    """
)
AGREEMENT_SQL = text(
    """
    SELECT item_type, field, qc_sample, count(*) AS n, avg(agrees::int) AS rate
    FROM app.labelled_example GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
    """
)


class DayCount(BaseModel):
    day: date
    item_type: str
    reviewed: int


class ReviewTime(BaseModel):
    item_type: str
    reviewed: int
    median_hours: float


class Agreement(BaseModel):
    """Reviewer vs model agreement on one field: QC sample and other items apart."""

    item_type: str
    field: str
    qc_examples: int
    qc_agreement: float | None
    other_examples: int
    other_agreement: float | None


class ReviewMetrics(BaseModel):
    queue_by_type: dict[str, int]
    reviewed_per_day: list[DayCount]
    time_to_review: list[ReviewTime]
    agreement: list[Agreement]


def agreement_rows(conn: Connection) -> list[Agreement]:
    """One row per (item type, field), QC and other columns side by side."""
    cells: dict[tuple[str, str], dict[bool, tuple[int, float]]] = {}
    for r in conn.execute(AGREEMENT_SQL):
        cells.setdefault((r.item_type, r.field), {})[bool(r.qc_sample)] = (r.n, float(r.rate))
    rows = []
    for (item_type, field), by_qc in sorted(cells.items()):
        qc_n, qc_rate = by_qc.get(True, (0, None))
        other_n, other_rate = by_qc.get(False, (0, None))
        rows.append(Agreement(item_type=item_type, field=field, qc_examples=qc_n,
                              qc_agreement=qc_rate, other_examples=other_n,
                              other_agreement=other_rate))  # fmt: skip
    return rows


def metrics(conn: Connection) -> ReviewMetrics:
    """Queue size, throughput, review time and agreement for the caller's center."""
    waiting = Counter(p.item_type for p in pending(conn, ITEM_TYPES))
    return ReviewMetrics(
        queue_by_type={t: waiting.get(t, 0) for t in ITEM_TYPES},
        reviewed_per_day=[DayCount(day=r.day, item_type=r.item_type, reviewed=r.n)
                          for r in conn.execute(PER_DAY_SQL)],
        time_to_review=[ReviewTime(item_type=r.item_type, reviewed=r.n,
                                   median_hours=round(float(r.hours), 2))
                        for r in conn.execute(MEDIAN_SQL)],
        agreement=agreement_rows(conn),
    )  # fmt: skip
