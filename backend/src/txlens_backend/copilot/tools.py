"""The copilot's tools: thin wrappers over the platform's own services and guards.

Each call runs in its own tenant transaction (RLS: the caller's center only), releases its
counts through suppression and the differencing guard for EVERY role (the copilot only ever
sees aggregates), and writes an audit row (tool, arguments, SQL, row count; never values).
A tool returns:
- `data`: what the model sees (aggregates, concept ids, abstracts), wrapped as untrusted data;
- `display`: what the UI shows (attrition, survival curves, a table);
- `error`: why it did not run, phrased so the planner can correct itself.
"""

import time
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Engine

from txlens_backend import audit
from txlens_backend.audit import RequestInfo
from txlens_backend.config import Settings
from txlens_backend.copilot import actions, sql_exec, sql_guard
from txlens_backend.copilot.embeddings import Embedder
from txlens_backend.copilot.embeddings import max_cost as embed_cost
from txlens_backend.copilot.retrieval import PgVectorStore, hybrid_search
from txlens_backend.db import tenant_transaction
from txlens_backend.documents.budget import Budget
from txlens_backend.services import cohorts, concepts, disclosure
from txlens_backend.survival import release, service
from txlens_backend.survival.analysis import Report
from txlens_backend.survival.suppression import ShownCurve

PUBMED = "https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
ABSTRACT_CHARS = 3000  # of each abstract the model reads (most abstracts fit whole)
LITERATURE_K = 6
SURVIVAL_DAYS = (365, 1095, 1825)


class Citation(BaseModel):
    """A retrieved abstract the answer may cite: shown with a verbatim snippet."""

    pmid: int
    title: str
    journal: str
    year: int | None
    snippet: str
    url: str


class ToolResult(BaseModel):
    """One tool call, as the model, the UI and the audit log see it."""

    tool: str
    args: dict[str, Any]
    ok: bool
    data: dict[str, Any] = {}
    display: dict[str, Any] | None = None
    sql: str | None = None
    citations: list[Citation] = []
    notices: list[str] = []
    error: str | None = None
    latency_ms: int = 0


@dataclass(frozen=True)
class ToolContext:
    """Who is asking, where, and with what."""

    engine: Engine
    tenant_id: str
    user_id: str
    settings: Settings
    info: RequestInfo
    message_id: str
    embedder: Embedder
    budget: Budget


def guard_for(conn: Any, ctx: ToolContext) -> disclosure.Disclosure:
    """The differencing guard for this user (every role: the copilot sees aggregates only)."""
    return disclosure.Disclosure(
        conn, ctx.user_id, "copilot", threshold=ctx.settings.small_cell_threshold,
        window_days=ctx.settings.disclosure_window_days,
    )  # fmt: skip


def concept_search(ctx: ToolContext, action: actions.ConceptSearch) -> ToolResult:
    """Standard SNOMED condition concepts by name or code (vocabulary only, no patients)."""
    with tenant_transaction(ctx.engine, ctx.tenant_id) as conn:
        hits = concepts.search_conditions(conn, action.query, 10)
    data = {"concepts": [h.model_dump() for h in hits]}
    return ToolResult(tool=action.tool, args=action.model_dump(), ok=True, data=data)


def preview_cohort(ctx: ToolContext, action: actions.PreviewCohort) -> ToolResult:
    """Patients passing the rules, step by step, suppressed and differencing-guarded."""
    args = action.model_dump(mode="json")
    if not cohorts.whole_years(action.rules):
        error = "transplant_date bounds must be whole years (Jan 1 / Dec 31)"
        return ToolResult(tool=action.tool, args=args, ok=False, error=error)
    with tenant_transaction(ctx.engine, ctx.tenant_id) as conn:
        _, steps = cohorts.preview(conn, action.rules)
        guard = guard_for(conn, ctx)
        constraints = cohorts.step_constraints(action.rules)
        shown = cohorts.release_counts(guard, list(zip(constraints, (n for _, n in steps),
                                                       strict=True)))  # fmt: skip
        record(conn, ctx, action.tool, args, rows=len(steps))
    preview = cohorts.Preview(
        count=shown[-1], notices=[guard.notice] if guard.notice else [],
        steps=[cohorts.Step(rule=label, count=n)
               for (label, _), n in zip(steps, shown, strict=True)],
    )  # fmt: skip
    data = preview.model_dump()
    return ToolResult(tool=action.tool, args=args, ok=True, data=data, notices=preview.notices,
                      display={"kind": "attrition", "preview": data})  # fmt: skip


def km_at(curve: ShownCurve, day: int) -> float | None:
    """The displayed curve's estimate at a day (its last step by then), or None."""
    shown = [p.estimate for p in curve.points if p.day <= day]
    return shown[-1] if shown and curve.points[-1].day >= day else None


def percent(value: float | None) -> float | None:
    """A proportion as a percentage with one decimal."""
    return None if value is None else round(100 * value, 1)


def survival_summary(report: Report) -> dict[str, Any]:
    """What the model needs from a report: counts, survival at 1/3/5 years, tests, HRs."""
    return {
        "population": report.population, "grouping": report.grouping_label,
        "landmark_days": report.landmark_days, "n": report.n, "events": report.events,
        "groups": [{"group": c.group, "n": c.n, "graft_loss_events": c.events,
                    "hidden_reason": c.hidden_reason,
                    "graft_survival_percent": {f"day_{d}": percent(km_at(c, d))
                                               for d in SURVIVAL_DAYS}}
                   for c in report.km],
        "log_rank_p": report.log_rank.p_value if report.log_rank else None,
        "cox": [{"term": t.label, "hazard_ratio": t.hazard_ratio, "ci95": [t.lower, t.upper],
                 "p": t.p_value, "ph_violated": t.ph_violated}
                for t in (report.cox.terms if report.cox else [])],
        "warnings": report.warnings,
        "synthetic_data": report.synthetic_data,
    }  # fmt: skip


def run_survival(ctx: ToolContext, action: actions.RunSurvival) -> ToolResult:
    """The survival service on inline rules (nothing saved), guarded for every role."""
    args = action.model_dump(mode="json")
    request = service.AnalysisRequest(rules=action.rules, grouping=action.grouping,
                                      landmark_days=action.landmark_days,
                                      covariates=action.covariates)  # fmt: skip
    with tenant_transaction(ctx.engine, ctx.tenant_id) as conn:
        try:
            report = service.run(conn, request, ctx.settings.small_cell_threshold)
        except service.LandmarkTooEarlyError as error:
            return ToolResult(tool=action.tool, args=args, ok=False, error=str(error))
        report = release.release(report, request, action.rules, None, guard_for(conn, ctx))
        record(conn, ctx, action.tool, args, rows=len(report.km))
    return ToolResult(
        tool=action.tool, args=args, ok=True, data=survival_summary(report),
        notices=report.warnings, display={"kind": "survival", "report": report.model_dump()},
    )  # fmt: skip


def sql_query(ctx: ToolContext, action: actions.SqlQuery) -> ToolResult:
    """Guard-checked aggregate SQL; a rejection explains which rule it broke."""
    args = action.model_dump()
    try:
        checked = sql_guard.check(action.sql)
    except sql_guard.SqlRejectedError as error:
        with tenant_transaction(ctx.engine, ctx.tenant_id) as conn:
            record(conn, ctx, action.tool, args, error=f"rejected: {error}")
        return ToolResult(tool=action.tool, args=args, ok=False, sql=action.sql,
                          error=f"SQL rejected by the guard: {error}")  # fmt: skip
    with tenant_transaction(ctx.engine, ctx.tenant_id) as conn:
        table = sql_exec.run(ctx.engine, ctx.tenant_id, checked, guard_for(conn, ctx))
        record(conn, ctx, action.tool, args | {"sql": checked.sql}, rows=len(table.rows))
    data = {"columns": table.columns, "rows": table.rows, "notices": table.notices}
    return ToolResult(tool=action.tool, args=args, ok=True, data=data, sql=checked.sql,
                      notices=table.notices, display={"kind": "table", **data})  # fmt: skip


def literature_search(ctx: ToolContext, action: actions.LiteratureSearch) -> ToolResult:
    """Hybrid retrieval over the abstracts; the embedding is paid from the copilot budget."""
    args = action.model_dump()
    reserved, cost = embed_cost(ctx.embedder.model, [action.query]), 0.0
    ctx.budget.reserve(reserved)
    try:
        with tenant_transaction(ctx.engine, ctx.tenant_id) as conn:
            passages, cost = hybrid_search(PgVectorStore(conn), ctx.embedder, action.query,
                                           LITERATURE_K)  # fmt: skip
            record(conn, ctx, action.tool, args, rows=len(passages))
    finally:
        ctx.budget.settle(reserved, cost)
    abstracts = [{"pmid": p.pmid, "title": p.title, "year": p.year, "journal": p.journal,
                  "abstract": p.abstract[:ABSTRACT_CHARS]} for p in passages]  # fmt: skip
    citations = [Citation(pmid=p.pmid, title=p.title, journal=p.journal, year=p.year,
                          snippet=p.snippet, url=PUBMED.format(pmid=p.pmid))
                 for p in passages]  # fmt: skip
    return ToolResult(tool=action.tool, args=args, ok=True, data={"abstracts": abstracts},
                      citations=citations)  # fmt: skip


def record(conn: Any, ctx: ToolContext, tool: str, args: dict[str, Any], *, rows: int = 0,
           error: str | None = None) -> None:  # fmt: skip
    """Audit one tool call: what was asked, never what came back."""
    details = {"tool": tool, "args": args, "rows": rows, "error": error}
    audit.record(conn, ctx.info, ctx.user_id, "copilot_tool_call", "copilot_message",
                 ctx.message_id, details)  # fmt: skip


def run(ctx: ToolContext, action: actions.ToolAction) -> ToolResult:
    """Run one tool and time it."""
    started = time.monotonic()
    match action:
        case actions.ConceptSearch():
            result = concept_search(ctx, action)
        case actions.PreviewCohort():
            result = preview_cohort(ctx, action)
        case actions.RunSurvival():
            result = run_survival(ctx, action)
        case actions.SqlQuery():
            result = sql_query(ctx, action)
        case actions.LiteratureSearch():
            result = literature_search(ctx, action)
    result.latency_ms = round((time.monotonic() - started) * 1000)
    return result
