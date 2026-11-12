"""Survival counts through the differencing guard (services/disclosure.py), for every role.

The report is already small-cell suppressed. Each remaining count (patients, events, numbers
at risk, exposed patients, observed events) is one cell, named by its place in the report,
e.g. `risk_table[group=kidney].cells[day=365].n_risk`. Its filters are the population's rules;
everything else that shapes the count (grouping, comparison cohort, landmark, covariates)
goes into the base, so only otherwise identical analyses are compared.
"""

from collections.abc import Iterator
from typing import Any

from txlens_backend.services import cohorts, disclosure
from txlens_backend.survival.analysis import Report
from txlens_backend.survival.service import AnalysisRequest

FAMILY = "survival"
COUNT_KEYS = frozenset({"n", "events", "n_risk", "n_exposed", "events_exposed"})
Path = tuple[str | int, ...]


def label(item: Any, index: int) -> str:
    """How a list element is named in a cell: by its group, day or key, else its index."""
    if isinstance(item, dict):
        for key in ("group", "day", "key"):
            if key in item:
                return f"{key}={item[key]}"
    return str(index)


def counts(node: Any, path: Path = (), name: str = "") -> Iterator[tuple[Path, str, int]]:
    """(where, cell name, value) of every exact count in a dumped report."""
    if isinstance(node, dict):
        for key, value in node.items():
            inner = f"{name}.{key}" if name else str(key)
            is_count = key in COUNT_KEYS or path[-1:] == ("observed",)
            if is_count and type(value) is int:
                yield (*path, key), inner, value
            else:
                yield from counts(value, (*path, key), inner)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            yield from counts(item, (*path, i), f"{name}[{label(item, i)}]")


def base(request: AnalysisRequest, report: Report, compare: cohorts.CohortRules | None) -> str:
    """Everything about the analysis except the population's filters."""
    return disclosure.base_key(
        grouping=request.grouping, landmark=report.landmark_days,
        covariates=sorted(request.covariates),
        compare=compare.model_dump(mode="json") if compare else None,
    )  # fmt: skip


def release(
    report: Report, request: AnalysisRequest, rules: cohorts.CohortRules | None,
    compare: cohorts.CohortRules | None, guard: disclosure.Disclosure,
) -> Report:  # fmt: skip
    """The report with differencing counts withheld (and a warning saying why)."""
    dumped = report.model_dump(mode="json")
    found = list(counts(dumped))
    constraints = cohorts.step_constraints(rules)[-1] if rules else {}
    cells = [disclosure.Cell(name, constraints, value) for _, name, value in found]
    shown = guard.release(FAMILY, base(request, report, compare), cells)
    for (path, _, _), value in zip(found, shown, strict=True):
        parent: Any = dumped
        for step in path[:-1]:
            parent = parent[step]
        parent[path[-1]] = value
    if guard.notice:
        dumped["warnings"] = [*dumped["warnings"], guard.notice]
    return Report.model_validate(dumped)
