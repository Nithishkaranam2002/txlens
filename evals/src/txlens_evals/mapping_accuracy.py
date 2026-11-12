"""Free-text condition mapping vs the codes the sending systems attached.

Centers A and C send most FHIR Conditions with a SNOMED code and a text. Hiding the code and
mapping the text alone, then comparing with the code, measures mapping accuracy against a
truth the mapper never saw. Two systems:
- vocabulary only: exact name or synonym, same words in another order, fuzzy (>= 0.90),
  each behind the qualifier guard;
- curated + vocabulary: the pipeline's matcher, with the TXLENS_CONDITION rows (curated by
  an AI assistant, pending clinical review) tried first.
No LLM maps conditions in TxLens, so there is no LLM system here.

Two kinds of rows are scored differently:
- the same concept: diabetes, hypertension and graft failure texts. Correct when the mapped
  concept is the code's concept or a more specific one (a descendant);
- the cause: the primary-disease entry is coded as end-stage renal disease or heart
  failure, but its text names the cause ("IgA nephropathy", "ischemic cardiomyopathy").
  Correct when the mapped concept is the code's concept or lies in that organ's disease
  hierarchy (kidney disease, heart disease).
Intervals resample distinct texts (the same text repeats across patients). `make
mapping-report` writes evals/reports/mapping_accuracy.md and its headline JSON.
"""

import argparse
import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import Connection, text

from txlens_backend.config import get_settings
from txlens_backend.db import make_engine
from txlens_evals.stats import Estimate, bootstrap, ratio, write_headline
from txlens_pipelines.textmatch.matcher import ConceptMatcher, Match, Term

REPORT = Path("evals/reports/mapping_accuracy.md")
ENGLISH = 4180186
# Codes whose text names the cause, and the disease hierarchy the cause must fall in.
CAUSE_ROOTS = {"46177005": ("SNOMED", "90708001"),  # ESRD -> disorder of kidney
               "84114007": ("SNOMED", "56265001")}  # heart failure -> heart disease  # fmt: skip
SYSTEMS = ("vocabulary only", "curated + vocabulary")
CANDIDATES_SQL = text(
    "SELECT concept_id, concept_name FROM vocab.concept WHERE vocabulary_id = 'SNOMED' "
    "AND domain_id IN ('Condition', 'Observation') AND invalid_reason IS NULL"
)
SYNONYMS_SQL = text(
    "SELECT s.concept_id, s.concept_synonym_name FROM vocab.concept_synonym s "
    "JOIN vocab.concept c USING (concept_id) WHERE s.language_concept_id = :english "
    "AND c.vocabulary_id = 'SNOMED' AND c.domain_id IN ('Condition', 'Observation') "
    "AND c.invalid_reason IS NULL"
)
CURATED_SQL = text(
    "SELECT source_code, target_concept_id FROM vocab.source_to_concept_map "
    "WHERE source_vocabulary_id = 'TXLENS_CONDITION' AND invalid_reason IS NULL"
)
CODE_SQL = text(
    "SELECT concept_code, concept_id FROM vocab.concept WHERE vocabulary_id = 'SNOMED' "
    "AND concept_code = ANY(:codes)"
)
DESCENDANTS_SQL = text(
    "SELECT ancestor_concept_id, descendant_concept_id FROM vocab.concept_ancestor "
    "WHERE ancestor_concept_id = ANY(:ids)"
)


@dataclass(frozen=True)
class Coded:
    """One coded FHIR Condition: its center, code and text."""

    center: str
    code: str
    text: str


def read_coded(folder: Path) -> list[Coded]:
    """Every Condition that has both a SNOMED code and a text."""
    found = []
    for path in sorted(folder.glob("center_*/*.json")):
        bundle = json.loads(path.read_text(encoding="utf-8"))
        entries = bundle.get("entry", []) if isinstance(bundle, dict) else bundle
        for entry in entries:
            resource = entry.get("resource", entry)
            if resource.get("resourceType") != "Condition":
                continue
            code = resource.get("code", {})
            codings = code.get("coding") or []
            if codings and code.get("text"):
                found.append(Coded(path.parent.name, codings[0]["code"], code["text"]))
    return found


def matchers(conn: Connection) -> dict[str, ConceptMatcher]:
    """The two systems, built from the loaded vocabulary as the pipeline builds its own."""
    preferred = {int(i): str(n) for i, n in conn.execute(CANDIDATES_SQL)}
    terms = [Term(i, n) for i, n in preferred.items()]
    terms += [Term(int(i), str(n)) for i, n in conn.execute(SYNONYMS_SQL, {"english": ENGLISH})]
    curated = {str(c): int(t) for c, t in conn.execute(CURATED_SQL)}
    return {"vocabulary only": ConceptMatcher(terms, preferred, {}),
            "curated + vocabulary": ConceptMatcher(terms, preferred, curated)}  # fmt: skip


@dataclass(frozen=True)
class Truth:
    """Concept ids of the codes, and the concepts that count as correct for each code."""

    code_id: dict[str, int]
    accepted: dict[str, frozenset[int]]


def truth_sets(conn: Connection, codes: Iterable[str]) -> Truth:
    """Correct = the code's concept and its descendants; for cause codes, the organ's whole
    disease hierarchy too."""
    wanted = sorted(set(codes) | {root for _, root in CAUSE_ROOTS.values()})
    code_id = {str(c): int(i) for c, i in conn.execute(CODE_SQL, {"codes": wanted})}
    below: dict[int, set[int]] = defaultdict(set)
    for ancestor, descendant in conn.execute(DESCENDANTS_SQL, {"ids": list(code_id.values())}):
        below[int(ancestor)].add(int(descendant))
    accepted = {}
    for code in codes:
        ids = {code_id[code], *below[code_id[code]]} if code in code_id else set()
        if code in CAUSE_ROOTS:
            root = code_id.get(CAUSE_ROOTS[code][1])
            ids |= below[root] if root else set()
        accepted[code] = frozenset(ids)
    return Truth(code_id, accepted)


@dataclass(frozen=True)
class Scored:
    """One distinct (code, text) and how a system mapped it."""

    code: str
    text: str
    rows: int
    match: Match
    outcome: str  # "exact", "more specific", "in hierarchy", "wrong", "unmapped"


def outcome(match: Match, code: str, truth: Truth) -> str:
    if match.concept_id is None:
        return "unmapped"
    if match.concept_id == truth.code_id.get(code):
        return "exact"
    if match.concept_id in truth.accepted.get(code, frozenset()):
        return "in hierarchy" if code in CAUSE_ROOTS else "more specific"
    return "wrong"


def score(coded: list[Coded], matcher: ConceptMatcher, truth: Truth) -> list[Scored]:
    rows = Counter((c.code, c.text) for c in coded)
    out = []
    for (code, source), n in sorted(rows.items()):
        match = matcher.match(source)
        out.append(Scored(code, source, n, match, outcome(match, code, truth)))
    return out


def estimate(scored: list[Scored], kind: str, *, correct: bool = True) -> Estimate:
    """Share of rows (correct, or unmapped), with an interval over distinct texts."""
    good = ("exact", "more specific", "in hierarchy") if correct else ("unmapped",)
    units = [(s.rows * (s.outcome in good), s.rows) for s in scored if kind_of(s.code) == kind]
    return bootstrap(units, ratio(0, 1))


def kind_of(code: str) -> str:
    return "cause" if code in CAUSE_ROOTS else "same concept"


def render(coded: list[Coded], results: dict[str, list[Scored]]) -> str:
    centers = Counter(c.center for c in coded)
    lines = [
        "# Condition mapping: free text vs the codes the centers sent",
        "",
        f"Generated {date.today().isoformat()} by `make mapping-report` "
        "(evals/src/txlens_evals/mapping_accuracy.py). Synthetic data only.",
        "",
        f"{len(coded):,} coded FHIR Conditions ("
        + ", ".join(f"{k}: {v:,}" for k, v in sorted(centers.items()))
        + f"), {len({c.text for c in coded}):,} distinct texts. The code is hidden, the text "
        "is mapped, and the result is compared with the code. 95% intervals resample distinct "
        "texts (1,000 times).",
        "",
        "| System | Same concept: correct | Same concept: unmapped | Cause: in the organ's "
        "hierarchy | Cause: unmapped |",
        "|---|---|---|---|---|",
    ]
    for system, scored in results.items():
        lines.append(
            f"| {system} | {estimate(scored, 'same concept').text()} | "
            f"{estimate(scored, 'same concept', correct=False).text()} | "
            f"{estimate(scored, 'cause').text()} | "
            f"{estimate(scored, 'cause', correct=False).text()} |"
        )
    pipeline = results["curated + vocabulary"]
    by_method: dict[str, Counter[str]] = defaultdict(Counter)
    for s in pipeline:
        by_method[s.match.method][s.outcome] += s.rows
    outcomes = ("exact", "more specific", "in hierarchy", "wrong", "unmapped")
    lines += [
        "",
        "- Same concept: diabetes, hypertension and graft failure (correct = the code's "
        "concept or a more specific one). Cause: the primary-disease entry, coded as ESRD or "
        "heart failure but naming the cause (correct = within kidney or heart disease).",
        "- Unmapped texts go to the review queue; they are never guessed.",
        "",
        "## Pipeline matcher by method (rows)",
        "",
        "| Method | " + " | ".join(outcomes) + " |",
        "|---|" + "---|" * len(outcomes),
        *(f"| {m} | " + " | ".join(f"{c[o]:,}" for o in outcomes) + " |"
          for m, c in sorted(by_method.items())),
        "",
        "## Wrong or unmapped texts (pipeline matcher)",
        "",
        "| Code | Text | Rows | Mapped to | Outcome |",
        "|---|---|---|---|---|",
        *(f"| {s.code} | {s.text} | {s.rows:,} | {s.match.concept_id or '-'} | {s.outcome} |"
          for s in pipeline if s.outcome in ("wrong", "unmapped")),
        "",
        "## Honest limits",
        "",
        "- The texts come from one generator with a short list of diseases, and the curated "
        "rows were written knowing those texts: they are AI-curated and pending clinical "
        "review. The codes are an independent check of those rows, not of real problem lists.",
        "- Center B sends no codes, so its rows cannot be scored this way; it uses the same "
        "texts as A and C.",
        "",
    ]  # fmt: skip
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--fhir", type=Path, default=Path("data/raw/fhir"))
    parser.add_argument("--out", type=Path, default=REPORT)
    args = parser.parse_args(argv)
    coded = read_coded(args.fhir)
    engine = make_engine(get_settings().app_database_url.get_secret_value())
    with engine.connect() as conn:
        systems = matchers(conn)
        truth = truth_sets(conn, {c.code for c in coded})
    engine.dispose()
    results = {name: score(coded, systems[name], truth) for name in SYSTEMS}
    args.out.write_text(render(coded, results), encoding="utf-8")
    write_headline(args.out.with_suffix(".json"), {
        "coded_rows": len(coded),
        "systems": {name: {"same_concept_correct": estimate(s, "same concept").as_dict(),
                           "cause_in_hierarchy": estimate(s, "cause").as_dict(),
                           "unmapped": estimate(s, "same concept", correct=False).as_dict()}
                    for name, s in results.items()},
    })  # fmt: skip
    print(args.out.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
