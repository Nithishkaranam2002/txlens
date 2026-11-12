"""Biopsy report extraction: rules only vs LLM only vs hybrid, field by field, with 95% CIs.

Every system reads the same OCR text of the same reports (the full run of Step 2.1, stored in
the database), so no new model call is made:
- rules only: regular expressions over the OCR text (biopsy_rules.py), no LLM;
- LLM only: the first LLM pass as the model returned it: no second pass, no evidence check,
  no validation, no review;
- hybrid: what the pipeline produced (two passes, quotes checked against the OCR line,
  Banff/ISHLT validation, review routing), before any human edit.

A field is scored like a slot: right value = TP; a value that is wrong or not in the report =
FP; a reported value missed or wrong = FN. Intervals resample whole reports (1,000 times).
Cost and latency are the LLM's (OCR is the same for every system and is excluded); cost is
what the calls cost without the cache. `make extraction-systems` writes
evals/reports/extraction_systems.md and its headline JSON.
"""

import argparse
import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, text

from txlens_backend.config import get_settings
from txlens_backend.db import check_role_is_rls_bound, make_engine, tenant_transaction
from txlens_backend.db_setup import center_tenants
from txlens_backend.documents import store
from txlens_backend.documents.llm import price_per_token
from txlens_backend.documents.schema import heart_diagnosis
from txlens_evals import biopsy_rules
from txlens_evals.biopsy_extraction import HEART_FIELDS, KIDNEY_FIELDS, Truth, read_truth
from txlens_evals.stats import Estimate, bootstrap, f1, precision, ratio, recall, write_headline

SYSTEMS = ("rules only", "LLM only", "hybrid")
REPORT = Path("evals/reports/extraction_systems.md")
# One document per scanned report: the latest (earlier sample runs also registered
# prompt-injection variants of a few reports under the same path).
DOCUMENTS_SQL = text(
    "SELECT DISTINCT ON (source_path) document_id::text, source_path, organ, status "
    "FROM app.document WHERE status <> 'failed' AND organ IS NOT NULL "
    "ORDER BY source_path, created_at DESC"
)
PASSES_SQL = text(
    """
    SELECT DISTINCT ON (pass) pass AS variant, model, output, input_tokens, output_tokens,
           latency_ms, cached
    FROM app.extraction WHERE document_id = :d AND output IS NOT NULL
    ORDER BY pass, created_at DESC
    """
)
HYBRID_SQL = text("SELECT field, extracted_value FROM app.extracted_field WHERE document_id = :d")


@dataclass
class Report:
    """One scanned report: its truth and what each system read."""

    truth: Truth
    organ: str
    in_review: bool
    values: dict[str, dict[str, Any]] = field(default_factory=dict)  # system -> field -> value
    llm_cost: dict[str, float] = field(default_factory=dict)  # system -> $ (no cache)
    llm_ms: dict[str, list[int]] = field(default_factory=dict)  # system -> paid pass latencies
    rules_ms: float = 0.0

    @property
    def fields(self) -> tuple[str, ...]:
        return HEART_FIELDS if self.organ == "heart" else KIDNEY_FIELDS


def norm(value: Any) -> str | None:
    """Values compared as text: dates ISO, numbers as digits, empty as None."""
    if value is None or value == "":
        return None
    return value.isoformat() if isinstance(value, date) else str(value)


def llm_values(output: dict[str, Any], organ: str) -> dict[str, Any]:
    """The values of one raw LLM answer (a heart diagnosis follows from the grades by rule)."""
    values = {name: (finding or {}).get("value") for name, finding in output.items()}
    if organ == "heart":
        values["diagnosis"] = heart_diagnosis(values.get("acr_grade"), values.get("pamr_grade"))
    return values


def cost(row: Any) -> float:
    """What one pass costs without the cache."""
    price_in, price_out = price_per_token(row.model)
    return float(row.input_tokens * price_in + row.output_tokens * price_out)


def read_reports(engine: Engine, truths: dict[str, Truth], tenants: list[str]) -> list[Report]:
    """Every stored report that has a ground truth, with each system's values."""
    reports = []
    for tenant in tenants:
        with tenant_transaction(engine, tenant) as conn:
            for doc in conn.execute(DOCUMENTS_SQL).all():
                truth = truths.get(doc.source_path)
                if truth is not None:
                    reports.append(read_one(conn, doc, truth))
    return reports


def read_one(conn: Connection, doc: Any, truth: Truth) -> Report:
    report = Report(truth, doc.organ, doc.status == "in_review")
    ocr = store.ocr_of(conn, doc.document_id)
    started = time.perf_counter()
    report.values["rules only"] = biopsy_rules.extract(ocr, doc.organ)
    report.rules_ms = (time.perf_counter() - started) * 1000
    passes = {r.variant: r for r in conn.execute(PASSES_SQL, {"d": doc.document_id}).all()}
    first = passes.get("a")
    report.values["LLM only"] = llm_values(first.output, doc.organ) if first else {}
    rows = conn.execute(HYBRID_SQL, {"d": doc.document_id})
    hybrid: dict[str, Any] = {r.field: r.extracted_value for r in rows}
    report.values["hybrid"] = hybrid
    report.llm_cost = {"rules only": 0.0, "LLM only": cost(first) if first else 0.0,
                       "hybrid": sum(cost(p) for p in passes.values())}  # fmt: skip
    paid = [p for p in passes.values() if not p.cached]
    report.llm_ms = {"rules only": [], "LLM only": [first.latency_ms] if first and not
                     first.cached else [], "hybrid": [p.latency_ms for p in paid]}  # fmt: skip
    return report


def counts(report: Report, system: str, only: str | None = None) -> tuple[int, int, int]:
    """(tp, fp, fn) of one system on one report (all fields, or one)."""
    tp = fp = fn = 0
    for name in report.fields if only is None else (only,):
        truth = norm(report.truth.values.get(name))
        found = norm(report.values[system].get(name))
        tp += found is not None and found == truth
        fp += found is not None and found != truth
        fn += truth is not None and found != truth
    return tp, fp, fn


def fully_correct(report: Report, system: str) -> bool:
    return all(norm(report.values[system].get(n)) == norm(report.truth.values.get(n))
               for n in report.fields)  # fmt: skip


@dataclass(frozen=True)
class SystemResult:
    """Headline metrics of one system."""

    precision: Estimate
    recall: Estimate
    f1: Estimate
    fully_correct: Estimate
    diagnosis_f1: Estimate
    review_share: float
    wrong_loaded: int  # reports with a wrong field that reach OMOP without a person
    cost_per_report: float
    latency_p50_ms: float | None


def evaluate(reports: Sequence[Report], system: str) -> SystemResult:
    units = [(*counts(r, system), fully_correct(r, system), 1) for r in reports]
    diagnosis = [counts(r, system, "diagnosis") for r in reports]
    latencies = [ms for r in reports for ms in r.llm_ms[system]]
    if system == "rules only":
        latencies_ms = statistics.median(r.rules_ms for r in reports) if reports else None
    else:
        latencies_ms = statistics.median(latencies) if latencies else None
    reviewed = sum(r.in_review for r in reports) if system == "hybrid" else 0
    routed = system == "hybrid"
    wrong_loaded = sum(not fully_correct(r, system) and not (routed and r.in_review)
                       for r in reports)  # fmt: skip
    return SystemResult(
        precision=bootstrap(units, precision), recall=bootstrap(units, recall),
        f1=bootstrap(units, f1), fully_correct=bootstrap(units, ratio(3, 4)),
        diagnosis_f1=bootstrap(diagnosis, f1),
        review_share=reviewed / len(reports) if reports else 0.0, wrong_loaded=wrong_loaded,
        cost_per_report=sum(r.llm_cost[system] for r in reports) / max(len(reports), 1),
        latency_p50_ms=latencies_ms,
    )  # fmt: skip


def field_rows(reports: Sequence[Report]) -> list[str]:
    """Per organ and field: P / R / F1 for each system (point estimates)."""
    lines = ["| Organ | Field | " + " | ".join(f"{s}: P / R / F1" for s in SYSTEMS) + " |",
             "|---|---|" + "---|" * len(SYSTEMS)]  # fmt: skip
    for organ, names in (("kidney", KIDNEY_FIELDS), ("heart", HEART_FIELDS)):
        group = [r for r in reports if r.organ == organ]
        for name in names:
            cells = []
            for system in SYSTEMS:
                totals = [sum(c) for c in zip(*(counts(r, system, name) for r in group),
                                              strict=True)] or [0, 0, 0]  # fmt: skip
                cells.append(f"{precision(totals):.1%} / {recall(totals):.1%} / {f1(totals):.1%}")
            lines.append(f"| {organ} | {name} | " + " | ".join(cells) + " |")
    return lines


def ms(value: float | None) -> str:
    if value is None:
        return "-"
    if value >= 1000:
        return f"{value / 1000:.1f} s"
    return f"{value:.1f} ms" if value >= 1 else f"{value * 1000:.0f} µs"


def render(reports: Sequence[Report], results: dict[str, SystemResult]) -> str:
    kidney = sum(r.organ == "kidney" for r in reports)
    head = ["| System | Precision | Recall | F1 | Reports fully correct | Diagnosis F1 | "
            "Sent to review | Wrong reports loaded without review | LLM cost / report | "
            "Latency (p50) |",
            "|---|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for system, res in results.items():
        review = f"{res.review_share:.1%}" if system == "hybrid" else "none (no routing)"
        head.append(
            f"| {system} | {res.precision.text()} | {res.recall.text()} | {res.f1.text()} | "
            f"{res.fully_correct.text()} | {res.diagnosis_f1.text()} | {review} | "
            f"{res.wrong_loaded:,} | ${res.cost_per_report:.5f} | {ms(res.latency_p50_ms)} |"
        )
    return "\n".join([
        "# Biopsy extraction: rules only vs LLM only vs hybrid",
        "",
        f"Generated {date.today().isoformat()} by `make extraction-systems` "
        "(evals/src/txlens_evals/biopsy_systems.py). Synthetic data only.",
        "",
        f"{len(reports):,} scanned reports ({kidney:,} kidney, {len(reports) - kidney:,} heart), "
        "the same OCR text for every system. 95% bootstrap intervals in brackets (reports "
        "resampled 1,000 times). No model was called: the LLM answers are the stored ones.",
        "",
        "## Headline",
        "",
        *head,
        "",
        "- Fully correct: every field of the report equals the truth.",
        "- Sent to review: only the hybrid routes reports to a person; the other two would "
        "load every answer as it is. Wrong reports loaded without review is the number that "
        "reaches OMOP unchecked: for the hybrid, wrong reports it did not send to review.",
        "- Latency: rules only is the extraction itself; LLM latency is per paid call (the "
        "hybrid makes two in parallel); OCR, the same for all, is left out.",
        "",
        "## Per field (precision / recall / F1)",
        "",
        *field_rows(reports),
        "",
        "## Honest limits",
        "",
        "- One generator draws every report from one template, so the rules know every "
        "label. On real reports with other layouts the rules would fail first; the LLM "
        "would degrade more gracefully. The gap here is a lower bound on the LLM's value.",
        "- Most remaining errors come from OCR (simulated scans), not from either reader.",
        "- The hybrid's extra value is less in its accuracy than in its routing: what it is "
        "unsure of goes to a person instead of into OMOP.",
        "",
    ])  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--truth", type=Path, default=Path("data/ground_truth/biopsies.csv"))
    parser.add_argument("--out", type=Path, default=REPORT)
    args = parser.parse_args(argv)
    engine = make_engine(get_settings().app_database_url.get_secret_value())
    check_role_is_rls_bound(engine)
    truths = {t.pdf_file: t for t in read_truth(args.truth)}
    reports = read_reports(engine, truths, list(center_tenants()))
    engine.dispose()
    results = {system: evaluate(reports, system) for system in SYSTEMS}
    args.out.write_text(render(reports, results), encoding="utf-8")
    write_headline(args.out.with_suffix(".json"), {
        "reports": len(reports),
        "systems": {s: {"f1": r.f1.as_dict(), "fully_correct": r.fully_correct.as_dict(),
                        "diagnosis_f1": r.diagnosis_f1.as_dict(), "review_share": r.review_share,
                        "wrong_loaded": r.wrong_loaded,
                        "cost_per_report": r.cost_per_report, "latency_p50_ms": r.latency_p50_ms}
                    for s, r in results.items()},
    })  # fmt: skip
    print(args.out.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
