"""Run guard-checked copilot SQL and release its result: suppressed, differencing-guarded.

The query runs in its own transaction that is always rolled back:
`SET TRANSACTION READ ONLY`, the caller's tenant (RLS), `SET LOCAL ROLE txlens_copilot` (may
read only the copilot views and two vocabulary tables) and a statement timeout. It is sent
with a bind parameter, which makes the driver use the extended protocol: one statement only.

Each row is a cell of `n` patients. Rows with n of 1-10 show "<11" for every aggregate; other
counts go through the differencing guard, with the row's GROUP BY values as extra filters.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, text

from txlens_backend.copilot.sql_guard import MAX_ROWS, Checked, Output
from txlens_backend.db import set_tenant
from txlens_backend.services import disclosure

FAMILY = "sql"
ROLE = "txlens_copilot"
SUPPRESSED_NOTICE = 'Counts of 1-{last} patients are shown as "<{threshold}".'


@dataclass(frozen=True)
class Table:
    """A released result: column names, rows, and why any value is not shown."""

    columns: list[str]
    rows: list[list[Any]]
    notices: list[str]


def fetch(engine: Engine, tenant_id: str, checked: Checked, timeout_ms: int) -> list[list[Any]]:
    """The raw result rows (read-only, copilot role, tenant RLS, timeout, row limit)."""
    sql = f"SELECT * FROM ({checked.sql.replace('%', '%%')}) AS q LIMIT %s"
    with engine.connect() as conn, conn.begin() as transaction:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        set_tenant(conn, tenant_id)
        conn.execute(text(f"SET LOCAL ROLE {ROLE}"))
        conn.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
        rows = [list(r) for r in conn.exec_driver_sql(sql, (MAX_ROWS,)).all()]
        transaction.rollback()
    return [[float(v) if isinstance(v, Decimal) else v for v in row] for row in rows]


def row_constraints(checked: Checked, row: list[Any]) -> disclosure.Constraints:
    """The query's filters plus this row's GROUP BY values."""
    constraints = dict(checked.constraints)
    for output, value in zip(checked.outputs, row, strict=True):
        if output.kind != "group":
            continue
        if value is None:
            pinned: disclosure.Constraint = {"atom": True}
            dim = f"atom:{output.dim} IS NULL"
        else:
            pinned = {"eq": value} if isinstance(value, str | bool) else disclosure.interval(
                float(value), float(value))  # fmt: skip
            dim = output.dim
        constraints = disclosure.add(constraints, dim, pinned)
    return constraints


def release_row(
    checked: Checked, row: list[Any], guard: disclosure.Disclosure
) -> list[Any]:  # fmt: skip
    """One row as shown."""
    outputs: list[Output] = checked.outputs
    n = next(v for o, v in zip(outputs, row, strict=True) if o.kind == "n")
    if 0 < n < guard.threshold:
        hidden = f"<{guard.threshold}"
        return [v if o.kind == "group" else hidden for o, v in zip(outputs, row, strict=True)]
    counted = [i for i, o in enumerate(outputs) if o.kind in ("n", "count")]
    constraints = row_constraints(checked, row)
    cells = [disclosure.Cell(outputs[i].cell, constraints, int(row[i])) for i in counted]
    shown = dict(zip(counted, guard.release(FAMILY, checked.base, cells), strict=True))
    withheld = any(v == disclosure.WITHHELD for v in shown.values())
    return [
        shown.get(i, disclosure.WITHHELD if withheld and o.kind == "value" else v)
        for i, (o, v) in enumerate(zip(outputs, row, strict=True))
    ]


def run(
    engine: Engine, tenant_id: str, checked: Checked, guard: disclosure.Disclosure,
    timeout_ms: int = 5000,
) -> Table:  # fmt: skip
    """Run the query and release its rows to the guard's user."""
    rows = [release_row(checked, row, guard) for row in fetch(engine, tenant_id, checked,
                                                               timeout_ms)]  # fmt: skip
    notices = []
    if any(v == f"<{guard.threshold}" for row in rows for v in row):
        notices.append(SUPPRESSED_NOTICE.format(last=guard.threshold - 1,
                                                threshold=guard.threshold))  # fmt: skip
    if guard.notice:
        notices.append(guard.notice)
    return Table([o.name for o in checked.outputs], rows, notices)
