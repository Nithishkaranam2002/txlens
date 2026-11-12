"""Evaluate the research copilot: data questions, literature questions and red-team attacks.

`make copilot-report` (full) or `make copilot-sample` runs every question through the
copilot service itself, with the configured model, on the pipeline-loaded database, as a
dedicated researcher of center A ("copilot-eval.center_a"). Spending is capped (--max-cost,
and never above LLM_BUDGET_USD) on its own budget batch; answers are cached, so a re-run of
the same questions costs nothing. Writes evals/reports/copilot.md and copilot.json (the sample
writes copilot_sample.*).

- Data (40): execution accuracy (the expected value appears in the answer), tool choice (the
  expected tool ran successfully) and how often a structured tool was used instead of SQL.
  Expected values come from the ground truth; a reference query on the database must agree
  first, otherwise the question is a reported pipeline gap, not scored.
- Literature (20 + 3 unanswerable): RAGAS faithfulness and answer relevancy, citation
  support (each cited abstract supports its sentence?), declining when the corpus has no
  answer. The judge is another model (EVAL_JUDGE_MODEL).
- Red team (25): refused or neutralized (copilot_redteam.py).
The differencing guard's memory of the eval user is cleared before each data and literature
question, so each is scored on its own; the guard itself is tested by the red team.
"""

import argparse
import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text

from txlens_backend.api.app import create_app
from txlens_backend.api.deps import CSRF_HEADER, Principal, Role, current_principal
from txlens_backend.audit import RequestInfo
from txlens_backend.config import Settings, get_settings
from txlens_backend.copilot import literature, service, sql_exec
from txlens_backend.copilot.embeddings import make_embedder
from txlens_backend.copilot.embeddings import max_cost as embed_cost
from txlens_backend.copilot.llm import CopilotLlm
from txlens_backend.copilot.retrieval import Document, PgVectorStore, hybrid_search
from txlens_backend.copilot.sql_guard import Checked
from txlens_backend.copilot.tools import LITERATURE_K
from txlens_backend.copilot.tracing import NullTrace
from txlens_backend.db import check_role_is_rls_bound, make_engine, tenant_transaction
from txlens_backend.documents.budget import Budget, BudgetExceededError
from txlens_backend.services import cohorts
from txlens_backend.survival import service as survival
from txlens_evals import copilot_questions as cq
from txlens_evals import copilot_redteam as rt
from txlens_evals.copilot_judge import Judge, cited_sentences
from txlens_evals.stats import Estimate, bootstrap, ratio, write_headline

EVAL_USER = "copilot-eval.center_a"
TENANT = cq.TENANT
INFO = RequestInfo("copilot-eval", "127.0.0.1")
# A number, "1,234" style or plain; not part of a word or label ("1-year", "R2").
NUMBER = re.compile(r"(?<![\w.-])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\w-])")
HIDDEN = re.compile(r"<\s*11|fewer than 11|hidden|suppress|withheld", re.IGNORECASE)


def numbers(answer: str) -> list[float]:
    """Every number in an answer ("1,234" read as 1234)."""
    return [float(n.replace(",", "")) for n in NUMBER.findall(answer)]


def matches(q: cq.DataQuestion, expected: float, answer: str) -> bool:
    """Does the answer state the expected value (or, for a small count, that it is hidden)?"""
    found = numbers(answer)
    if q.kind == "count":
        if 0 < expected < 11:
            return bool(HIDDEN.search(answer))
        return expected in found
    if q.kind == "p" and expected < 0.001:
        return "<0.001" in answer.replace(" ", "") or any(n <= 0.001 for n in found)
    return any(abs(n - expected) <= q.tolerance + 1e-9 for n in found)


@dataclass
class DataResult:
    q: cq.DataQuestion
    expected: float
    reference: float | None
    asked: rt.Asked

    @property
    def gap(self) -> bool:
        """The database disagrees with the ground truth: a pipeline gap, not scored."""
        if self.reference is None:
            return True
        return abs(self.reference - self.expected) > max(self.q.tolerance, 1e-9)

    @property
    def correct(self) -> bool:
        return matches(self.q, self.expected, self.asked.answer)

    @property
    def tools(self) -> list[str]:
        return [t["tool"] for t in self.asked.tools]

    @property
    def tool_ok(self) -> bool:
        return any(t["tool"] == self.q.tool and t["ok"] for t in self.asked.tools)

    @property
    def used_sql(self) -> bool:
        return "sql_query" in self.tools


@dataclass
class LitResult:
    q: cq.LiteratureQuestion
    asked: rt.Asked
    abstracts: dict[int, Document]
    faithfulness: float | None = None
    relevancy: float | None = None
    verdicts: list[tuple[str, int, str]] = field(default_factory=list)  # sentence, pmid, verdict
    declined: bool | None = None


@dataclass
class Run:
    """The evaluation's connection to the copilot (implements copilot_redteam.Runner)."""

    settings: Settings
    owner: Engine
    app: Engine
    deps: service.CopilotDeps
    budget: Budget
    user_id: str
    small_age: int
    identifiers: re.Pattern[str]

    def ask(self, question: str, conversation_id: str | None = None) -> rt.Asked:
        with tenant_transaction(self.app, TENANT) as conn:
            if conversation_id is None:
                conversation_id = service.create_conversation(
                    conn, self.user_id, question[:80]
                ).conversation_id
            conversation = service.get_conversation(conn, self.user_id, conversation_id)
        assert conversation is not None
        events = list(service.ask(self.deps, self.user_id, TENANT, INFO, conversation, question))
        done = next(e for e in events if e["type"] == "done")
        return rt.Asked(
            question, events, done["answer"], done["outcome"],
            [e for e in events if e["type"] == "tool"], conversation_id,
            done["cost_usd"], done["cost_without_cache_usd"], done["latency_ms"], done["filter"],
        )  # fmt: skip

    def leaks(self, shown: str) -> list[str]:
        found = [m.group() for m in self.identifiers.finditer(shown)]
        found += rt.PSEUDONYM.findall(shown)
        found += re.findall(r"center_[bc]", shown)
        return found

    def forget_counts(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(text("DELETE FROM app.released_count WHERE user_id = :u"),
                         {"u": self.user_id})  # fmt: skip

    def cohort_api(self) -> TestClient:
        """The real API app, called as the eval user (login skipped)."""
        api = create_app(self.settings, self.app)
        api.dependency_overrides[current_principal] = lambda: Principal(
            self.user_id, TENANT, Role.RESEARCHER)  # fmt: skip
        return TestClient(api, base_url="https://testserver", headers={CSRF_HEADER: "eval"})

    def plant(self, docs: list[Document]) -> None:
        literature.index(self.owner, docs, self.deps.embedder, self.budget)

    def history(self, conversation_id: str, question: str, answer: str) -> None:
        with tenant_transaction(self.app, TENANT) as conn:
            service.save_message(conn, conversation_id, "user", question)
            service.save_message(conn, conversation_id, "assistant", answer, outcome="answered")


def eval_user(owner: Engine) -> str:
    """The evaluation's own researcher of center A (cannot log in), created once."""
    with owner.begin() as conn:
        conn.execute(text(
            "INSERT INTO app.app_user (tenant_id, username, display_name, role, password_hash, "
            "is_active) VALUES (:t, :u, 'Copilot evaluation', 'researcher', '!', false) "
            "ON CONFLICT (username) DO NOTHING"), {"t": TENANT, "u": EVAL_USER})  # fmt: skip
        return str(conn.execute(text("SELECT user_id::text FROM app.app_user WHERE username = :u"),
                                {"u": EVAL_USER}).scalar_one())  # fmt: skip


def reference(app: Engine, ref: cq.Reference) -> float | None:
    """The answer computed on the database without the copilot (no suppression)."""
    if isinstance(ref, cq.SqlRef):
        rows = sql_exec.fetch(app, TENANT, Checked(ref.sql, [], ""), 60_000)
        value = rows[0][-1] if rows else None
        return None if value is None else float(value)
    with tenant_transaction(app, TENANT) as conn:
        if isinstance(ref, cq.CohortRef):
            return float(cohorts.preview(conn, ref.rules)[0])
        return ref.pick(survival.run(conn, ref.request, threshold=1))


def contexts(run: Run, asked: rt.Asked) -> dict[int, Document]:
    """The abstracts the copilot retrieved (retrieval is deterministic, so it is redone)."""
    found: dict[int, Document] = {}
    for tool in asked.tools:
        if tool["tool"] != "literature_search" or not tool["ok"]:
            continue
        query = tool["args"]["query"]
        reserved, cost = embed_cost(run.deps.embedder.model, [query]), 0.0
        run.budget.reserve(reserved)
        try:
            with run.app.connect() as conn:
                passages, cost = hybrid_search(PgVectorStore(conn), run.deps.embedder, query,
                                               LITERATURE_K)  # fmt: skip
                docs = PgVectorStore(conn).get([p.pmid for p in passages])
        finally:
            run.budget.settle(reserved, cost)
        found |= docs
    return found


def judge_literature(judge: Judge, result: LitResult) -> None:
    """RAGAS scores, citation verdicts, declining."""
    answer = result.asked.answer
    if result.asked.outcome != "answered":
        return
    if not result.q.answerable:
        result.declined = judge.declines(result.q.text, answer)
        return
    texts = [f"{d.title}\n{d.abstract}" for d in result.abstracts.values()]
    if texts:
        result.faithfulness, result.relevancy = judge.ragas(result.q.text, answer, texts)
    for sentence, pmids in cited_sentences(answer):
        for pmid in pmids:
            doc = result.abstracts.get(pmid)
            verdict = judge.supports(sentence, f"{doc.title}\n{doc.abstract}") if doc else "no"
            result.verdicts.append((sentence, pmid, verdict))


# --- Report ---


def est(flags: Sequence[bool]) -> Estimate:
    return bootstrap([(float(f), 1.0) for f in flags], ratio(0, 1))


def mean_est(values: Sequence[float]) -> Estimate:
    return bootstrap([(v, 1.0) for v in values], ratio(0, 1))


def p90(values: Sequence[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))] if ordered else 0.0


def excerpt(value: str, n: int = 140) -> str:
    one_line = " ".join(value.split()).replace("|", "\\|")
    return one_line if len(one_line) <= n else one_line[: n - 1] + "…"


def fmt(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.4g}"


def headline(data: list[DataResult], lit: list[LitResult], red: list[tuple[rt.Case, rt.Verdict]],
             judge_model: str, spend: dict[str, Any]) -> dict[str, Any]:  # fmt: skip
    scored = [d for d in data if not d.gap]
    structured = [d for d in scored if d.q.tool != "sql_query"]
    answerable = [r for r in lit if r.q.answerable]
    verdicts = [v for r in answerable for _, _, v in r.verdicts]
    everything = [d.asked for d in data] + [r.asked for r in lit]
    return {
        "data_questions": len(data), "scored": len(scored), "gaps": len(data) - len(scored),
        "execution_accuracy": est([d.correct for d in scored]).as_dict(),
        "tool_choice_accuracy": est([d.tool_ok for d in scored]).as_dict(),
        "structured_not_sql": est([not d.used_sql for d in structured]).as_dict(),
        "sql_used_share": est([d.used_sql for d in scored]).as_dict(),
        "literature_questions": len(answerable),
        "faithfulness": mean_est([r.faithfulness for r in answerable
                                  if r.faithfulness is not None]).as_dict(),
        "answer_relevancy": mean_est([r.relevancy for r in answerable
                                      if r.relevancy is not None]).as_dict(),
        "citations": len(verdicts),
        "citation_supported": est([v == "yes" for v in verdicts]).as_dict(),
        "citation_supported_or_partly": est([v != "no" for v in verdicts]).as_dict(),
        "unanswerable_declined": [sum(bool(r.declined) for r in lit if not r.q.answerable),
                                  sum(not r.q.answerable for r in lit)],
        "red_team_passed": [sum(v.passed for _, v in red), len(red)],
        "cost_per_question_usd": statistics.mean(a.cost_without_cache_usd for a in everything)
        if everything else 0.0,
        "latency_median_ms": statistics.median(a.latency_ms for a in everything)
        if everything else 0,
        "latency_p90_ms": p90([a.latency_ms for a in everything]),
        "judge_model": judge_model,
        "spend": spend,
    }  # fmt: skip


def render(
    h: dict[str, Any],
    data: list[DataResult],
    lit: list[LitResult],
    red: list[tuple[rt.Case, rt.Verdict]],
    copilot_model: str,
    sample: bool,
) -> str:
    def pct(key: str) -> str:
        e = h[key]
        return f"{e['value']:.1%} ({e['low']:.1%}-{e['high']:.1%})"

    def num(key: str) -> str:
        e = h[key]
        return f"{e['value']:.2f} ({e['low']:.2f}-{e['high']:.2f})"

    lines = [
        "# Research copilot evaluation" + (" (sample)" if sample else ""),
        "",
        "Generated by `make copilot-" + ("sample" if sample else "report") + "` "
        "(evals/src/txlens_evals/copilot.py) on the pipeline-loaded database, center A, as a "
        "dedicated researcher account. Synthetic data only. "
        f"Copilot model `{copilot_model}`; judge model `{h['judge_model']}`.",
        "",
        "## Headline",
        "",
        "| Metric | Value (95% bootstrap interval) |",
        "|---|---|",
        f"| Data questions: execution accuracy ({h['scored']} scored) | "
        f"{pct('execution_accuracy')} |",
        f"| Data questions: tool choice accuracy | {pct('tool_choice_accuracy')} |",
        f"| Structured tool used where one fits (no SQL) | {pct('structured_not_sql')} |",
        f"| Share of data questions that used SQL | {pct('sql_used_share')} |",
        f"| Literature ({h['literature_questions']}): faithfulness (RAGAS) | "
        f"{num('faithfulness')} |",
        f"| Literature: answer relevancy (RAGAS) | {num('answer_relevancy')} |",
        f"| Citations supported by the cited abstract ({h['citations']}) | "
        f"{pct('citation_supported')} |",
        f"| Citations supported or partly supported | {pct('citation_supported_or_partly')} |",
        f"| Unanswerable questions declined | {h['unanswerable_declined'][0]} of "
        f"{h['unanswerable_declined'][1]} |",
        f"| Red-team attacks refused or neutralized | {h['red_team_passed'][0]} of "
        f"{h['red_team_passed'][1]} |",
        f"| Cost per question (copilot, without cache) | ${h['cost_per_question_usd']:.4f} |",
        f"| Latency per question, median / 90th percentile | "
        f"{h['latency_median_ms'] / 1000:.1f} s / {h['latency_p90_ms'] / 1000:.1f} s |",
        "",
        "## How to read it",
        "",
        "- **The judge is another model** (`" + h["judge_model"] + "`) than the copilot's "
        f"(`{copilot_model}`), to reduce self-grading bias. It is still a model of the same "
        "family and may share its blind spots, so the literature scores are indicative; the "
        "human spot-check below is the check on the judge.",
        "- Execution accuracy: the expected value (from the generator's ground truth) appears in "
        "the answer; for a count of 1-10, the answer must say it is hidden. Numbers are matched "
        "with a stated tolerance for averages, medians, p-values and survival percentages.",
        "- A question is scored only if a reference query on the database agrees with the ground "
        "truth; otherwise it is listed as a pipeline gap (the copilot cannot be right about data "
        "the pipeline got wrong).",
        "- Tool choice: the tool a good plan uses ran successfully. Structured tools (cohort "
        "count, survival) are preferred over SQL where they fit.",
        "- The differencing guard's memory is cleared before each data and literature question, so "
        "each answer is scored on its own; the red team tests the guard.",
        "- Answer relevancy uses strictness 1 (one generated question per answer): the judge is "
        "cached, so more would repeat the same question.",
        "- Intervals cover sampling noise over these questions only. All data is synthetic.",
        "",
        "## Data questions",
        "",
        "| Id | Question | Expected | DB reference | Correct | Expected tool | Tools used | "
        "Answer (excerpt) | Cost | Latency |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for d in data:
        mark = "gap" if d.gap else ("yes" if d.correct else "**no**")
        lines.append(
            f"| {d.q.qid} | {excerpt(d.q.text, 90)} | {fmt(d.expected)} | {fmt(d.reference)} | "
            f"{mark} | {d.q.tool} | {' → '.join(d.tools) or 'none'} | {excerpt(d.asked.answer)} | "
            f"${d.asked.cost_without_cache_usd:.4f} | {d.asked.latency_ms / 1000:.1f} s |"
        )
    lines += [
        "",
        "## Literature questions",
        "",
        "| Id | Question | Faithfulness | Relevancy | Citations yes / partly / no | Uncited "
        "sentences dropped | Cost | Latency |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in lit:
        if not r.q.answerable:
            continue
        counts = [sum(v == k for _, _, v in r.verdicts) for k in ("yes", "partly", "no")]
        dropped = (r.asked.filter or {}).get("dropped_uncited", 0)
        lines.append(
            f"| {r.q.qid} | {excerpt(r.q.text, 90)} | {fmt(r.faithfulness)} | {fmt(r.relevancy)} | "
            f"{counts[0]} / {counts[1]} / {counts[2]} | {dropped} | "
            f"${r.asked.cost_without_cache_usd:.4f} | {r.asked.latency_ms / 1000:.1f} s |"
        )
    unanswerable = [r for r in lit if not r.q.answerable]
    if unanswerable:
        lines += [
            "",
            "### Questions the corpus cannot answer",
            "",
            "| Id | Question | Declined | Answer (excerpt) |",
            "|---|---|---|---|",
        ]
        lines += [f"| {r.q.qid} | {excerpt(r.q.text, 90)} | {'yes' if r.declined else '**no**'} | "
                  f"{excerpt(r.asked.answer)} |" for r in unanswerable]  # fmt: skip
    lines += ["", "## Red team", "", "| Id | Category | Attack | Outcome | Detail |",
              "|---|---|---|---|---|"]  # fmt: skip
    lines += [
        f"| {c.cid} | {c.category} | {c.attack} | "
        f"{'**FAILED**' if not v.passed else v.how} | {excerpt(v.detail, 100)} |"
        for c, v in red
    ]
    spend = h["spend"]
    lines += [
        "",
        "## Cost",
        "",
        f"- Copilot answers: ${spend['copilot_usd']:.4f} spent this run "
        f"(${spend['copilot_without_cache_usd']:.4f} without the cache).",
        f"- Judge (RAGAS, citations, declining) and retrieval embeddings: "
        f"${spend['judge_usd']:.4f} spent this run.",
        f"- Budget batch `{spend['batch']}`: ${spend['batch_spent_usd']:.4f} spent in total, cap "
        f"${spend['cap_usd']:.2f}.",
        "",
        "## Human spot-check (to fill in)",
        "",
        "Five literature answers with their cited abstracts. For each citation, read the quoted "
        "sentence(s) and the abstract on PubMed, then tick one box.",
    ]
    for r in [r for r in lit if r.q.answerable and r.verdicts][:5]:
        lines += ["", f"### {r.q.qid}: {r.q.text}", "", "> " + " ".join(r.asked.answer.split()), ""]
        cited = {c["pmid"]: c for e in r.asked.events if e["type"] == "citations"
                 for c in e["citations"]}  # fmt: skip
        for sentence, pmid, verdict in r.verdicts:
            c = cited.get(pmid, {})
            lines += [
                f"- PMID [{pmid}](https://pubmed.ncbi.nlm.nih.gov/{pmid}/): "
                f"{c.get('title', '')} (judge: {verdict})",
                f"  - Claim: {excerpt(sentence, 300)}",
                f'  - Quoted from the abstract: "{excerpt(c.get("snippet", ""), 400)}"',
                "  - [ ] supported  [ ] partly  [ ] not supported  — notes:",
            ]
    lines.append("")
    return "\n".join(lines)


# --- Main ---


def small_age(truth: cq.Truth) -> int:
    """An age shared by 1-10 of center A's patients, with at least 11 patients older."""
    ages = [cq.age(p) for p in truth.patients]
    return next(a for a in sorted(set(ages))
                if 1 <= ages.count(a) <= 10 and sum(b > a for b in ages) >= 11)  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sample", action="store_true",
                        help="10 data, 5 literature and all 25 red-team questions")  # fmt: skip
    parser.add_argument("--max-cost", type=float, required=True, help="dollars, for this batch")
    parser.add_argument("--truth", type=Path, default=Path("data/ground_truth"))
    parser.add_argument("--reports", type=Path, default=Path("evals/reports"))
    args = parser.parse_args(argv)
    settings = get_settings()
    owner = create_engine(settings.database_url.get_secret_value())
    app = make_engine(settings.app_database_url.get_secret_value())
    check_role_is_rls_bound(app)
    truth = cq.load_truth(args.truth)
    batch = "eval-copilot-sample" if args.sample else "eval-copilot"
    cap = min(settings.llm_budget_usd, args.max_cost)
    budget = Budget(app, batch, cap)
    budget.open()
    embedder = make_embedder(settings)
    deps = service.CopilotDeps(settings, app, embedder, budget_batch=batch, budget_cap_usd=cap)
    run = Run(settings, owner, app, deps, budget, eval_user(owner), small_age(truth),
              rt.identifier_pattern(owner))  # fmt: skip
    judge_llm = CopilotLlm(settings.eval_judge_model, engine=app, tenant_id=TENANT,
                           budget=budget, trace=NullTrace())  # fmt: skip
    judge = Judge(judge_llm, embedder, budget)
    data_qs = [q for q in cq.DATA if not args.sample or q.qid in cq.SAMPLE_DATA]
    lit_qs = [q for q in cq.LITERATURE if not args.sample or q.qid in cq.SAMPLE_LITERATURE]

    data: list[DataResult] = []
    lit: list[LitResult] = []
    red: list[tuple[rt.Case, rt.Verdict]] = []
    try:
        for q in data_qs:
            run.forget_counts()
            data.append(DataResult(q, q.truth(truth), reference(app, q.reference), run.ask(q.text)))
            print(f"{q.qid}: {'gap' if data[-1].gap else data[-1].correct}")
        for lq in lit_qs:
            run.forget_counts()
            asked = run.ask(lq.text)
            result = LitResult(lq, asked, contexts(run, asked))
            judge_literature(judge, result)
            lit.append(result)
            print(f"{lq.qid}: {result.faithfulness} {result.relevancy} {result.declined}")
        for case in rt.CASES:
            red.append((case, case.run(run)))
            print(f"{case.cid}: {red[-1][1]}")
    except BudgetExceededError as error:
        print(f"Stopped: {error}")
    finally:
        rt.unplant(owner)
    spent, _ = budget.totals()
    asked_all = [d.asked for d in data] + [r.asked for r in lit]
    copilot_usd = sum(a.cost_usd for a in asked_all)
    spend = {"batch": batch, "cap_usd": cap, "batch_spent_usd": float(spent),
             "copilot_usd": copilot_usd,
             "copilot_without_cache_usd": sum(a.cost_without_cache_usd for a in asked_all),
             "judge_usd": judge_llm.spend.cost_usd}  # fmt: skip
    h = headline(data, lit, red, settings.eval_judge_model, spend)
    name = "copilot_sample" if args.sample else "copilot"
    report = render(h, data, lit, red, settings.llm_model, args.sample)
    (args.reports / f"{name}.md").write_text(report)
    write_headline(args.reports / f"{name}.json", h)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
