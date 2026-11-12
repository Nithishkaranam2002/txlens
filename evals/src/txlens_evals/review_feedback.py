"""Reviewer decisions as an evaluation set: agreement with the model, and a regression check.

`make review-report` reads app.labelled_example (one row per reviewed field: the model's value,
the reviewer's value, de-identified evidence only) as the app login, center by center, and:
- writes evals/data/review_corrections.jsonl: every labelled example, the eval set that grows
  with each review (no identifiers: redacted lines, de-identified sentences, masked dates);
- reports agreement per item type and field, the QC sample apart: QC items were drawn at
  random from what the model would have auto-approved, so their agreement estimates the
  automatic path's accuracy; every other item was sent to review because it looked wrong;
- re-runs today's note rules on every reviewed note mention and counts how often they now
  give the reviewer's answer (a fix that breaks an earlier correction shows up here);
- counts what waits in each center's queue, by type (AI-curated rows are shared, so every
  center counts the same ones).
Biopsy and mapping examples are stored for the next extraction evaluation; re-scoring them
needs paid model calls, so this script does not.
"""

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import Engine, text

from txlens_backend.config import get_settings
from txlens_backend.db import check_role_is_rls_bound, make_engine, tenant_transaction
from txlens_backend.db_setup import center_tenants
from txlens_backend.notes.nlp import NoteNlp
from txlens_backend.notes.vocabulary import load_vocabulary
from txlens_backend.review.queue import pending
from txlens_backend.review.reasons import ITEM_TYPES

EXAMPLES_SQL = text(
    """
    SELECT e.item_type, e.field, e.model_value, e.reviewer_value, e.agrees, e.qc_sample,
           e.evidence_deid, d.action, d.reason_code, m.lexical_variant AS term
    FROM app.labelled_example e
    JOIN app.review_decision d ON d.decision_id = e.decision_id
    LEFT JOIN app.note_mention m ON d.item_type = 'note_mention'
         AND m.note_source_value = split_part(d.item_key, ':', 1)
         AND m.start_offset || '-' || m.end_offset = split_part(d.item_key, ':', 2)
    ORDER BY e.example_id
    """
)
# The attributes the rules decide (the concept comes from the vocabulary, not a rule).
RESCORED = ("negated", "temporality", "experiencer")


@dataclass(frozen=True)
class Example:
    """One reviewed field."""

    item_type: str
    field: str
    model_value: str | None
    reviewer_value: str | None
    agrees: bool
    qc_sample: bool
    evidence: str | None
    action: str
    reason_code: str | None
    term: str | None


def read_examples(engine: Engine, tenants: list[str]) -> list[Example]:
    examples = []
    for tenant in tenants:
        with tenant_transaction(engine, tenant) as conn:
            examples += [Example(*row) for row in conn.execute(EXAMPLES_SQL)]
    return examples


def waiting(engine: Engine, tenants: list[str]) -> dict[str, Counter[str]]:
    """center -> items waiting per type, read as the app login under RLS."""
    found: dict[str, Counter[str]] = {}
    for tenant in tenants:
        with tenant_transaction(engine, tenant) as conn:
            found[tenant] = Counter(str(p.item_type) for p in pending(conn, ITEM_TYPES))
    return found


def agreement(
    examples: list[Example],
) -> list[tuple[str, str, int, float | None, int, float | None]]:
    """(item type, field, QC n, QC rate, other n, other rate)."""
    cells: dict[tuple[str, str], dict[bool, list[bool]]] = defaultdict(lambda: defaultdict(list))
    for e in examples:
        cells[(e.item_type, e.field)][e.qc_sample].append(e.agrees)
    rows = []
    for (item_type, field), by_qc in sorted(cells.items()):
        qc, other = by_qc[True], by_qc[False]
        rows.append((item_type, field, len(qc), sum(qc) / len(qc) if qc else None,
                     len(other), sum(other) / len(other) if other else None))  # fmt: skip
    return rows


def rules_value(nlp: NoteNlp, sentence: str, term: str, field: str) -> str | None:
    """What today's rules say about `term` in this sentence (None if they miss it)."""
    note = f"NOTE\n\n{sentence}\n"
    for m in nlp.extract(note, "kidney"):
        if m.text.lower() == term.lower():
            return {"negated": "true" if m.negated else "false", "temporality": m.temporality,
                    "experiencer": m.experiencer}[field]  # fmt: skip
    return None


def rescore(nlp: NoteNlp | None, examples: list[Example]) -> tuple[int, int]:
    """(reviewed note-mention attributes, how many the rules now get right)."""
    checked = [e for e in examples if e.item_type == "note_mention" and e.field in RESCORED
               and e.action != "reject" and e.term and e.evidence]  # fmt: skip
    if nlp is None or not checked:
        return len(checked), 0
    right = sum(rules_value(nlp, e.evidence or "", e.term or "", e.field) == e.reviewer_value
                for e in checked)  # fmt: skip
    return len(checked), right


def pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.1%}"


def render(
    examples: list[Example], rescored: tuple[int, int], queues: dict[str, Counter[str]]
) -> str:
    corrected = sum(not e.agrees for e in examples)
    lines = [
        "# Review queue: reviewer vs model agreement",
        "",
        f"Generated {date.today().isoformat()} by `make review-report` "
        "(evals/src/txlens_evals/review_feedback.py). Synthetic data only.",
        "",
        f"{len(examples):,} reviewed fields ({corrected:,} corrected by a reviewer), saved to "
        "evals/data/review_corrections.jsonl.",
        "",
        "| Item type | Field | QC sample: fields | QC: agreement | Other: fields | "
        "Other: agreement |",
        "|---|---|---|---|---|---|",
    ]
    lines += [f"| {t} | {f} | {qn:,} | {pct(qr)} | {on:,} | {pct(orate)} |"
              for t, f, qn, qr, on, orate in agreement(examples)]  # fmt: skip
    if not examples:
        lines.append("| (no reviews yet) | | | | | |")
    checked, right = rescored
    lines += [
        "",
        "QC-sample agreement is the honest estimate of the automatic path's accuracy; other "
        "items were sent to review because they looked wrong, so their agreement is lower by "
        "design.",
        "",
        "## Today's note rules on reviewed note mentions",
        "",
        f"{right:,} of {checked:,} reviewed attributes (negation, temporality, experiencer) "
        "now match the reviewer.",
        "",
        "## Waiting in each center's queue",
        "",
        "| Center | " + " | ".join(ITEM_TYPES) + " |",
        "|---|" + "---|" * len(ITEM_TYPES),
        *(f"| {center} | " + " | ".join(f"{counts.get(t, 0):,}" for t in ITEM_TYPES) + " |"
          for center, counts in sorted(queues.items())),
        "",
    ]  # fmt: skip
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--out", type=Path, default=Path("evals/reports/review_agreement.md"))
    parser.add_argument("--examples", type=Path,
                        default=Path("evals/data/review_corrections.jsonl"))  # fmt: skip
    args = parser.parse_args(argv)
    engine = make_engine(get_settings().app_database_url.get_secret_value())
    check_role_is_rls_bound(engine)
    tenants = list(center_tenants())
    examples = read_examples(engine, tenants)
    nlp = None
    if any(e.item_type == "note_mention" for e in examples):
        with tenant_transaction(engine, tenants[0]) as conn:
            nlp = NoteNlp(load_vocabulary(conn))
    report = render(examples, rescore(nlp, examples), waiting(engine, tenants))
    engine.dispose()
    args.examples.parent.mkdir(parents=True, exist_ok=True)
    args.examples.write_text("".join(json.dumps(asdict(e)) + "\n" for e in examples))
    args.out.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
