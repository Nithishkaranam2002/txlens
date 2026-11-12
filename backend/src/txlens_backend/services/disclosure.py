"""Differencing guard: no two counts shown to one user may differ by 1-10 over nested filters.

Small-cell suppression hides counts of 1-10, but two exact counts can still be subtracted:
"kidney patients aged >= 18" = 990 and "aged >= 19" = 985 reveal that 5 patients were 18.
Every count released to a user is therefore remembered (`app.released_count`, per user, for
a rolling window). A new count is withheld when an earlier one of the same kind (`family`,
`base`, `cell`) was over **overlapping filters** - one filter set a refinement of the other,
such as an added rule or a narrower range - and the two differ by 1 to threshold-1.

Filters are described as constraints by dimension (stored as JSON):
- `{"eq": v}`: equal to v;
- `{"range": [lo, lo_inclusive, hi, hi_inclusive]}` (None = open), numbers or ISO dates;
- `{"any": [...]}`: any of these values (a smaller set is narrower);
- `{"exists": [{"lab", "range", "window"}]}`: lab results meeting each rule in a day window;
- `{"atom": true}`: an opaque condition, equal only to itself (the dimension is its text).

X refines Y when every dimension of Y is implied by X's constraint on it (X may add more).
The guard covers the cohort builder, survival analysis and the copilot. Its limits (filters
written in incomparable ways, several accounts working together) are described in ADR 0018.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.expression import bindparam

from txlens_backend.deid import suppress

Constraint = dict[str, Any]
Constraints = dict[str, Constraint]
WITHHELD = "withheld"
WITHHELD_NOTICE = (
    "Some counts are withheld: together with a count you were shown in the last {days} days, "
    "over overlapping filters, they would reveal a group of fewer than {threshold} patients."
)


def interval(lo: Any = None, hi: Any = None, *, lo_inclusive: bool = True,
             hi_inclusive: bool = True) -> Constraint:  # fmt: skip
    """A range constraint; None leaves that end open."""
    return {"range": [lo, lo_inclusive, hi, hi_inclusive]}


def _lower_within(x: list[Any], y: list[Any]) -> bool:
    """x's lower bound is at least as tight as y's."""
    (x_lo, x_inc), (y_lo, y_inc) = x[:2], y[:2]
    if y_lo is None:
        return True
    if x_lo is None:
        return False
    return bool(x_lo > y_lo or (x_lo == y_lo and (y_inc or not x_inc)))


def _upper_within(x: list[Any], y: list[Any]) -> bool:
    """x's upper bound is at least as tight as y's."""
    (x_hi, x_inc), (y_hi, y_inc) = x[2:], y[2:]
    if y_hi is None:
        return True
    if x_hi is None:
        return False
    return bool(x_hi < y_hi or (x_hi == y_hi and (y_inc or not x_inc)))


def range_within(x: list[Any], y: list[Any]) -> bool:
    """Every value in range x is in range y."""
    return _lower_within(x, y) and _upper_within(x, y)


def _lab_implies(x: Mapping[str, Any], y: Mapping[str, Any]) -> bool:
    """A result meeting rule x (in its window) also meets rule y (in y's window)."""
    (x_from, x_to), (y_from, y_to) = x["window"], y["window"]
    return bool(
        x["lab"] == y["lab"]
        and range_within(x["range"], y["range"])
        and y_from <= x_from
        and x_to <= y_to
    )


def implies(x: Constraint, y: Constraint) -> bool:
    """Whatever meets constraint x also meets constraint y (same dimension)."""
    if "range" in x and "range" in y:
        return range_within(x["range"], y["range"])
    if "any" in x and "any" in y:
        return set(x["any"]) <= set(y["any"])
    if "exists" in x and "exists" in y:
        return all(any(_lab_implies(a, b) for a in x["exists"]) for b in y["exists"])
    return x == y


def refines(x: Constraints, y: Constraints) -> bool:
    """Every patient passing filters x also passes filters y."""
    return all(dim in x and implies(x[dim], c) for dim, c in y.items())


def overlapping(x: Constraints, y: Constraints) -> bool:
    """One filter set is nested in the other, so their counts can be subtracted."""
    return refines(x, y) or refines(y, x)


def conjoin(x: Constraint, y: Constraint) -> Constraint:
    """Both constraints on one dimension at once (as far as one constraint can say it)."""
    if "range" in x and "range" in y:
        lo = x["range"] if not _lower_within(y["range"], x["range"]) else y["range"]
        hi = x["range"] if not _upper_within(y["range"], x["range"]) else y["range"]
        return {"range": [lo[0], lo[1], hi[2], hi[3]]}
    if "any" in x and "any" in y:
        return {"any": sorted(set(x["any"]) & set(y["any"]), key=str)}
    if "exists" in x and "exists" in y:
        return {"exists": [*x["exists"], *y["exists"]]}
    return x


def add(constraints: Constraints, dim: str, constraint: Constraint) -> Constraints:
    """The constraints with one more, conjoined with any already on that dimension."""
    current = constraints.get(dim)
    return constraints | {dim: constraint if current is None else conjoin(current, constraint)}


def base_key(**parts: object) -> str:
    """A short stable key for everything about a count except its filters."""
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


@dataclass(frozen=True)
class Cell:
    """One count about to be shown: what it counts (`cell`), its filters, its value."""

    cell: str
    constraints: Constraints
    count: int


INSERT = text(
    "INSERT INTO app.released_count (user_id, source, family, base, cell, constraints, count) "
    "VALUES (:user, :source, :family, :base, :cell, :constraints, :count)"
).bindparams(bindparam("constraints", type_=JSONB))


class Disclosure:
    """Releases counts to one user: suppresses 1..threshold-1, withholds differencing ones."""

    def __init__(self, conn: Connection, user_id: str, source: str, *, threshold: int,
                 window_days: int) -> None:  # fmt: skip
        self.conn, self.user_id, self.source = conn, user_id, source
        self.threshold, self.window_days = threshold, window_days
        self.withheld = 0

    @property
    def notice(self) -> str | None:
        """Why counts were withheld, if any were."""
        if not self.withheld:
            return None
        return WITHHELD_NOTICE.format(days=self.window_days, threshold=self.threshold)

    def earlier(self, family: str, base: str) -> list[tuple[str, Constraints, int]]:
        """(cell, constraints, count) released to this user for this kind of count lately."""
        rows = self.conn.execute(
            text(
                "SELECT cell, constraints, count AS n FROM app.released_count "
                "WHERE user_id = :user AND family = :family AND base = :base "
                "AND released_at > now() - make_interval(days => :days)"
            ),
            {"user": self.user_id, "family": family, "base": base, "days": self.window_days},
        ).all()
        return [(r.cell, r.constraints, int(r.n)) for r in rows]

    def differences(self, cell: Cell, earlier: Sequence[tuple[str, Constraints, int]]) -> bool:
        """An earlier count of this cell, over overlapping filters, is 1..threshold-1 away."""
        return any(
            name == cell.cell
            and 0 < abs(count - cell.count) < self.threshold
            and overlapping(constraints, cell.constraints)
            for name, constraints, count in earlier
        )

    def release(self, family: str, base: str, cells: Sequence[Cell]) -> list[int | str]:
        """The counts as shown, in order. Each released count is remembered, and later cells
        of the same call are compared with earlier ones too (e.g. attrition steps)."""
        earlier = self.earlier(family, base)
        shown: list[int | str] = []
        for cell in cells:
            value = suppress(cell.count, self.threshold)
            if isinstance(value, int) and self.differences(cell, earlier):
                value = WITHHELD
                self.withheld += 1
            if isinstance(value, int):
                earlier.append((cell.cell, cell.constraints, cell.count))
                self.conn.execute(INSERT, {
                    "user": self.user_id, "source": self.source, "family": family,
                    "base": base, "cell": cell.cell, "constraints": cell.constraints,
                    "count": cell.count,
                })  # fmt: skip
            shown.append(value)
        return shown
