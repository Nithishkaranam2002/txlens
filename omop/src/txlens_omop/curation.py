"""Write review-queue decisions back into SOURCE_TO_CONCEPT_MAP.csv.

`txlens-vocab export-curation` reads, as the owner role:
- the review history of the AI-curated rows (app.curated_mapping_history; one history for
  every center). A row with a clinician approval or correction in force gets that concept and
  the label "[clinician-reviewed <date>, review h<id>]" in place of "[curated by AI
  assistant, pending clinical review]"; a row whose reviews were all reverted keeps (or gets
  back) the AI assistant's concept and the pending label;
- approved or edited condition-mapping gaps (app.review_decision). Each becomes a new
  TXLENS_CONDITION row labelled "[reviewed <date>, review d<id>]". If centers chose
  different concepts for the same text, nothing is written for it and the conflict is
  reported for a person to settle.
Run `make vocab-load-local` afterwards; the next pipeline run then applies the mappings to
every center. The file never holds reviewer names: only dates and review ids.
"""

import csv
import io
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg

from txlens_omop.mappings import (
    CURATED_PENDING_REVIEW,
    PENDING_REVIEW_LABEL,
    normalize_source_code,
)

PENDING = f"[{PENDING_REVIEW_LABEL}]"
LABEL = re.compile(r"\s*\[(?:curated by AI assistant[^\]]*|clinician-reviewed [^\]]*)\]$")
GAP_VOCABULARY = "TXLENS_CONDITION"
HISTORY_SQL = (
    "SELECT source_vocabulary_id, source_code, action, old_concept_id, new_concept_id, "
    "history_id, decided_at::date FROM app.curated_mapping_history ORDER BY history_id"
)
# Each center's latest decision on each gap text (the owner sees every center).
GAPS_SQL = """
    SELECT DISTINCT ON (tenant_id, item_key) item_key, action, changes -> 'final' ->> 'concept_id',
           decision_id::text, decided_at::date
    FROM app.review_decision WHERE item_type = 'condition_mapping'
    ORDER BY tenant_id, item_key, decided_at DESC
"""
VOCABULARY_SQL = "SELECT concept_id, vocabulary_id FROM vocab.concept WHERE concept_id = ANY(%s)"


@dataclass(frozen=True)
class Review:
    """Where a curated row stands: the concept in force and the review that set it."""

    concept_id: int
    reviewed_on: date
    history_id: int


@dataclass(frozen=True)
class GapMapping:
    """A reviewer's concept for a condition text (an approved or edited gap)."""

    text: str
    concept_id: int
    decided_on: date
    decision_id: str


def reviews(history: Iterable[Sequence[object]]) -> dict[tuple[str, str], Review | None]:
    """Replay each row's history (approve/correct push, revert pops): the review in force,
    or None when every review was reverted."""
    stacks: dict[tuple[str, str], list[Review]] = defaultdict(list)
    for vocabulary, code, action, _old, new, history_id, on in history:
        stack = stacks[(str(vocabulary), str(code))]
        if action == "revert":
            if stack:
                stack.pop()
        else:
            stack.append(Review(int(str(new)), on, int(str(history_id))))  # type: ignore[arg-type]
    return {key: (stack[-1] if stack else None) for key, stack in stacks.items()}


def gap_mappings(decisions: Iterable[Sequence[object]]) -> tuple[list[GapMapping], list[str]]:
    """(one mapping per text all centers agree on, texts with conflicting concepts)."""
    by_text: dict[str, list[GapMapping]] = defaultdict(list)
    for text, action, concept, decision_id, on in decisions:
        if action in ("approve", "edit") and concept:
            key = normalize_source_code(str(text))
            by_text[key].append(GapMapping(key, int(str(concept)), on, str(decision_id)))  # type: ignore[arg-type]
    agreed, conflicts = [], []
    for text, found in sorted(by_text.items()):
        if len({m.concept_id for m in found}) > 1:
            conflicts.append(text)
        else:
            agreed.append(max(found, key=lambda m: m.decided_on))
    return agreed, conflicts


def relabel(description: str, label: str) -> str:
    """The description with its review label replaced."""
    return f"{LABEL.sub('', description)} {label}"


def apply(
    rows: list[dict[str, str]], state: dict[tuple[str, str], Review | None],
    gaps: Sequence[GapMapping], vocabulary_of: dict[int, str],
) -> list[dict[str, str]]:  # fmt: skip
    """The mapping rows with curated rows relabelled (and corrected) and gap rows added."""
    out = []
    for row in rows:
        key = (row["source_vocabulary_id"], row["source_code"])
        if row["source_vocabulary_id"] in CURATED_PENDING_REVIEW and key in state:
            row = dict(row)
            review = state[key]
            if review is None:
                row["source_code_description"] = relabel(row["source_code_description"], PENDING)
            else:
                row["target_concept_id"] = str(review.concept_id)
                row["target_vocabulary_id"] = vocabulary_of[review.concept_id]
                label = f"[clinician-reviewed {review.reviewed_on.isoformat()}, review " \
                        f"h{review.history_id}]"  # fmt: skip
                row["source_code_description"] = relabel(row["source_code_description"], label)
        out.append(row)
    known = {(r["source_vocabulary_id"], r["source_code"]) for r in out}
    template = rows[0]
    for gap in gaps:
        if (GAP_VOCABULARY, gap.text) in known:
            continue
        known.add((GAP_VOCABULARY, gap.text))
        out.append(template | {
            "source_code": gap.text, "source_concept_id": "0",
            "source_vocabulary_id": GAP_VOCABULARY,
            "source_code_description": f"{gap.text} [reviewed {gap.decided_on.isoformat()}, "
                                       f"review d{gap.decision_id[:8]}]",
            "target_concept_id": str(gap.concept_id),
            "target_vocabulary_id": vocabulary_of[gap.concept_id],
            "valid_start_date": gap.decided_on.strftime("%Y%m%d"), "invalid_reason": "",
        })  # fmt: skip
    return out


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return list(reader.fieldnames or []), list(reader)


def write_rows(path: Path, header: list[str], rows: Iterable[dict[str, str]]) -> None:
    out = io.StringIO()
    writer = csv.DictWriter(out, header, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(out.getvalue(), encoding="utf-8")


def export(conn: psycopg.Connection[tuple[object, ...]], path: Path) -> tuple[int, int, list[str]]:
    """Rewrite the mapping file; returns (curated rows reviewed, gap rows added, conflicts)."""
    state = reviews(conn.execute(HISTORY_SQL).fetchall())
    gaps, conflicts = gap_mappings(conn.execute(GAPS_SQL).fetchall())
    ids = [r.concept_id for r in state.values() if r] + [g.concept_id for g in gaps]
    vocabulary_of = {int(str(i)): str(v) for i, v in conn.execute(VOCABULARY_SQL, (ids,))}
    header, rows = read_rows(path)
    new_rows = apply(rows, state, gaps, vocabulary_of)
    write_rows(path, header, new_rows)
    return sum(1 for r in state.values() if r), len(new_rows) - len(rows), conflicts
