"""Clinical-note NLP vs the generator's ground truth (data/ground_truth/note_facts.jsonl).

The NLP runs on what the pipeline loaded: the de-identified `cdm.note` text, read as the app
role under Row-Level Security. Predicted offsets are mapped back to the original text through
`app.note_phi_span`, where the ground truth offsets point. A prediction matches a fact when
the spans overlap and the category is the same (one-to-one).

Systems compared:
- rules-only: vocabulary matcher; every mention affirmed, current, about the patient;
- medspaCy: + sections and ConText (negation, history, hypothetical, family);
- hybrid: + the LLM for sentences the rules cannot resolve (needs an API key and a budget).

With --full, every metric is given twice: for the held-out notes (all notes except the 300
read while writing the rules; the headline numbers) and for those 300 tuning notes.

Precision is given twice: strict, and without predictions inside text the generator writes
but does not list as a fact (the primary disease, "ESRD 2/2 IgA nephropathy", and the
dd-cfDNA in "If dd-cfDNA rises above 1%"). Both are real mentions.
"""

import argparse
import csv
import json
import random
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import Connection, Engine, text

from txlens_backend.config import Settings
from txlens_backend.db import make_engine, tenant_transaction
from txlens_backend.documents.budget import Budget
from txlens_backend.documents.llm import make_extractor, price_per_token
from txlens_backend.notes.fallback import PROMPT, Fallback, FallbackRun
from txlens_backend.notes.nlp import Mention, NoteNlp
from txlens_backend.notes.store import NoteRow, phi_spans, read_notes
from txlens_backend.notes.vocabulary import load_vocabulary
from txlens_pipelines.deid_notes import Replacement, to_original_offset

CATEGORIES = ("condition", "medication", "lab", "procedure")
SAMPLE = 300
SEED = 7  # the 300 notes of seed 7 were read while writing the rules: the tuning notes
HELD_OUT = "held-out"
TUNING = "tuning"
REPORT = Path("evals/reports/notes_nlp.md")
# Ground-truth concept key -> codes whose concept (or any descendant) is a correct mapping.
# ("TxLens", code) are local concepts; an empty tuple means no concept exists (expect 0).
EXPECTED: dict[str, tuple[tuple[str, str], ...]] = {
    "kidney_transplant": (("SNOMED", "70536003"),),
    "heart_transplant": (("SNOMED", "32413006"),),
    "prior_transplant": (("SNOMED", "70536003"), ("SNOMED", "32413006")),
    "graft_biopsy": (("SNOMED", "1293269004"), ("SNOMED", "387829002")),
    "dgf": (("SNOMED", "426136000"),),
    "edema": (("SNOMED", "267038008"),),
    "fever": (("SNOMED", "386661006"),),
    "dysuria": (("SNOMED", "49650001"),),
    "dyspnea": (("SNOMED", "267036007"),),
    "palpitations": (("SNOMED", "80313002"),),
    "graft_tenderness": (),
    "esrd": (("SNOMED", "46177005"),),
    "hypertension": (("SNOMED", "38341003"),),
    "diabetes": (("SNOMED", "73211009"),),
    "heart_failure": (("SNOMED", "84114007"),),
    "coronary_artery_disease": (("SNOMED", "53741008"),),
    "polycystic_kidney_disease": (("SNOMED", "82525005"),),
    "cmv_infection": (("SNOMED", "28944009"), ("SNOMED", "1085381000119108")),
    "bk_nephropathy": (("SNOMED", "713886006"),),
    "acute_tubular_injury": (("SNOMED", "35455006"),),
    "rejection": (("SNOMED", "213148006"),),
    "tcmr": (("SNOMED", "236574008"), ("SNOMED", "432843002")),
    "abmr": (("SNOMED", "1381981002"),),
    "borderline_rejection": (("TxLens", "BORDERLINE_REJECTION"),),
    "mixed_rejection": (("SNOMED", "213148006"),),
    "tacrolimus": (("RxNorm", "42316"),),
    "mycophenolate mofetil": (("RxNorm", "68149"),),
    "prednisone": (("RxNorm", "8640"),),
    "methylprednisolone": (("RxNorm", "6902"),),
    "antithymocyte globulin": (("RxNorm", "1011"),),
    "immune globulin": (("RxNorm", "5666"),),
    "valganciclovir": (("RxNorm", "275891"),),
    "creatinine": (("LOINC", "2160-0"),),
    "egfr": (("LOINC", "98979-8"),),
    "tacrolimus_trough": (("LOINC", "77348-1"),),
    "dd_cfdna": (("LOINC", "103142-6"),),
    "dsa": (("TxLens", "DSA_MAX_MFI"),),
    "dsa_mfi": (("TxLens", "DSA_MAX_MFI"),),
}
# Text the generator writes without listing it as a fact: the primary disease ("ESRD 2/2 IgA
# nephropathy") and the dd-cfDNA in the plan's threshold sentence ("If dd-cfDNA rises above").
UNTRACKED = (re.compile(r"2/2 ([^\n]+)"), re.compile(r"If (dd-cfDNA) rises"))


@dataclass(frozen=True)
class Fact:
    """One ground-truth mention."""

    note_id: str
    concept: str
    category: str
    text: str
    start: int
    end: int
    negated: bool
    temporality: str
    experiencer: str


@dataclass
class Scores:
    """Counts for one system."""

    tp: Counter[str] = field(default_factory=Counter)
    fp: Counter[str] = field(default_factory=Counter)
    fn: Counter[str] = field(default_factory=Counter)
    fp_untracked: Counter[str] = field(default_factory=Counter)
    attribute_right: Counter[str] = field(default_factory=Counter)
    matched: int = 0
    errors: Counter[tuple[str, str, str]] = field(default_factory=Counter)
    seconds: float = 0.0


def read_facts(path: Path) -> dict[str, list[Fact]]:
    """Ground-truth facts by note id."""
    facts: dict[str, list[Fact]] = defaultdict(list)
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            r = json.loads(line)
            facts[r["note_id"]].append(
                Fact(
                    r["note_id"],
                    r["concept"],
                    r["category"],
                    r["text"],
                    r["start"],
                    r["end"],
                    r["negated"],
                    r["temporality"],
                    r["experiencer"],
                )
            )
    return facts


def note_tenants(data: Path) -> dict[str, str]:
    """note id -> tenant, via the patient."""
    with (data / "ground_truth" / "patients.csv").open(encoding="utf-8") as handle:
        tenant_of = {r["patient_id"]: r["tenant_id"] for r in csv.DictReader(handle)}
    with (data / "ground_truth" / "notes.csv").open(encoding="utf-8") as handle:
        return {r["note_id"]: tenant_of[r["patient_id"]] for r in csv.DictReader(handle)}


def choose(notes: dict[str, str], n: int | None, seed: int) -> dict[str, list[str]]:
    """Note ids per tenant: all, or a random sample of n (same seed, same sample)."""
    ids = sorted(notes)
    chosen = ids if n is None else sorted(random.Random(seed).sample(ids, min(n, len(ids))))
    by_tenant: dict[str, list[str]] = defaultdict(list)
    for note_id in chosen:
        by_tenant[notes[note_id]].append(note_id)
    return by_tenant


def split_of(chosen: dict[str, list[str]], tuning: set[str]) -> dict[str, dict[str, list[str]]]:
    """Note ids per split and tenant: held-out first (the headline), then the tuning notes."""
    splits: dict[str, dict[str, list[str]]] = {
        HELD_OUT: defaultdict(list),
        TUNING: defaultdict(list),
    }
    for tenant, ids in chosen.items():
        for note_id in ids:
            splits[TUNING if note_id in tuning else HELD_OUT][tenant].append(note_id)
    return {name: dict(by_tenant) for name, by_tenant in splits.items() if by_tenant}


def expected_ids(conn: Connection) -> dict[str, frozenset[int]]:
    """Ground-truth key -> concept ids that count as correct (the codes and descendants)."""
    resolved: dict[str, frozenset[int]] = {}
    for key, codes in EXPECTED.items():
        ids: set[int] = set()
        for vocabulary, code in codes:
            rows = conn.execute(
                text(
                    "SELECT c.concept_id, coalesce(a.descendant_concept_id, c.concept_id) "
                    "FROM vocab.concept c LEFT JOIN vocab.concept_ancestor a "
                    "ON a.ancestor_concept_id = c.concept_id "
                    "WHERE c.vocabulary_id = :v AND c.concept_code = :c"
                ),
                {"v": vocabulary, "c": code},
            )
            for own, descendant in rows:
                ids |= {int(own), int(descendant)}
        resolved[key] = frozenset(ids) if codes else frozenset({0})
    return resolved


def original_span(m: Mention, spans: Sequence[Replacement]) -> tuple[int, int]:
    """A mention's offsets in the original text."""
    return to_original_offset(spans, m.start), to_original_offset(spans, m.end - 1) + 1


def untracked_spans(original: str) -> list[tuple[int, int]]:
    """Text with no ground truth (see UNTRACKED)."""
    return [(m.start(1), m.end(1)) for p in UNTRACKED for m in p.finditer(original)]


def score_note(
    scores: Scores, mentions: Sequence[Mention], facts: Sequence[Fact],
    spans: Sequence[Replacement], untracked: Sequence[tuple[int, int]],
    expected: dict[str, frozenset[int]],
) -> None:  # fmt: skip
    """Add one note's matches and errors to `scores`."""
    left = list(facts)
    for m in mentions:
        start, end = original_span(m, spans)
        fact = next(
            (f for f in left if f.category == m.category and f.start < end and start < f.end),
            None,
        )
        if fact is None:
            scores.fp[m.category] += 1
            if any(s < end and start < e for s, e in untracked):
                scores.fp_untracked[m.category] += 1
            else:
                scores.errors[("false positive", m.category, m.text.lower())] += 1
            continue
        left.remove(fact)
        scores.tp[m.category] += 1
        scores.matched += 1
        checks = {
            "negation": m.negated == fact.negated,
            "temporality": m.temporality == fact.temporality,
            "experiencer": m.experiencer == fact.experiencer,
            "concept": m.concept_id in expected.get(fact.concept, frozenset()),
        }
        for name, right in checks.items():
            scores.attribute_right[name] += right
            if not right:
                scores.errors[(f"wrong {name}", fact.concept, fact.text.lower())] += 1
    for fact in left:
        scores.fn[fact.category] += 1
        scores.errors[("missed", fact.category, fact.text.lower())] += 1


# A system: (tenant, notes) -> mentions per note.
System = Callable[[str, list[NoteRow]], list[list[Mention]]]


def rules_system(nlp: NoteNlp) -> System:
    """rules-only or medspaCy: the pipeline alone."""
    return lambda _tenant, notes: nlp.extract_many([(n.text, n.organ) for n in notes])


def hybrid_system(nlp: NoteNlp, engine: Engine, settings: Settings, budget: Budget,
                  run: FallbackRun) -> System:  # fmt: skip
    """medspaCy plus the LLM fallback for unresolved sentences."""
    extractor = make_extractor(settings, "a", PROMPT)

    def extract(tenant: str, notes: list[NoteRow]) -> list[list[Mention]]:
        fallback = Fallback(engine, tenant, extractor, budget, nlp.vocabulary, run)
        rules = nlp.extract_many([(n.text, n.organ) for n in notes])
        return [fallback.resolve(n.text, n.organ, m) for n, m in zip(notes, rules, strict=True)]

    return extract


@dataclass
class Split:
    """Scores of every system on one set of notes."""

    name: str
    scores: dict[str, Scores]
    notes: int = 0
    facts: int = 0


def evaluate(
    engine: Engine, chosen: dict[str, list[str]], facts: dict[str, list[Fact]],
    systems: dict[str, System], name: str,
) -> Split:  # fmt: skip
    """Run every system on the chosen notes."""
    split = Split(name, {system: Scores() for system in systems})
    scores = split.scores
    for tenant, ids in sorted(chosen.items()):
        with tenant_transaction(engine, tenant) as conn:
            notes: list[NoteRow] = read_notes(conn, ids)
            spans = phi_spans(conn, [n.note_id for n in notes])
            expected = expected_ids(conn)
        split.notes += len(notes)
        split.facts += sum(len(facts.get(n.source_value, [])) for n in notes)
        for system_name, system in systems.items():
            started = time.perf_counter()
            results = system(tenant, notes)
            scores[system_name].seconds += time.perf_counter() - started
            for note, mentions in zip(notes, results, strict=True):
                own = spans.get(note.note_id, [])
                original = reidentified(note.text, own)
                score_note(scores[system_name], mentions, facts.get(note.source_value, []), own,
                           untracked_spans(original), expected)  # fmt: skip
    return split


def reidentified(deid: str, spans: Sequence[Replacement]) -> str:
    """The original note text (to find the untracked primary-disease span)."""
    from txlens_pipelines.deid_notes import reidentify

    return reidentify(deid, list(spans))


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    """Precision, recall, F1."""
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def pct(value: float) -> str:
    return f"{value:.1%}"


def system_rows(scores: dict[str, Scores]) -> list[str]:
    """Per system and category: P (strict), P (tracked text), R, F1."""
    lines = ["| System | Category | Precision | Precision (tracked text) | Recall | F1 |",
             "|---|---|---|---|---|---|"]  # fmt: skip
    for name, s in scores.items():
        for category in (*CATEGORIES, "all"):
            pick = CATEGORIES if category == "all" else (category,)
            tp = sum(s.tp[c] for c in pick)
            fp = sum(s.fp[c] for c in pick)
            fn = sum(s.fn[c] for c in pick)
            fp_tracked = fp - sum(s.fp_untracked[c] for c in pick)
            p, r, f1 = prf(tp, fp, fn)
            p_tracked = prf(tp, fp_tracked, fn)[0]
            label = f"**{category}**" if category == "all" else category
            lines.append(f"| {name} | {label} | {pct(p)} | {pct(p_tracked)} | {pct(r)} | "
                         f"{pct(f1)} |")  # fmt: skip
    return lines


def attribute_rows(scores: dict[str, Scores]) -> list[str]:
    """Per system: negation, temporality, experiencer and concept accuracy on matched facts."""
    names = ("negation", "temporality", "experiencer", "concept")
    lines = ["| System | Matched facts | " + " | ".join(n.capitalize() for n in names) + " |",
             "|---|---|" + "---|" * len(names)]  # fmt: skip
    for name, s in scores.items():
        cells = [pct(s.attribute_right[n] / s.matched) if s.matched else "-" for n in names]
        lines.append(f"| {name} | {s.matched:,} | " + " | ".join(cells) + " |")
    return lines


def error_rows(s: Scores, limit: int = 15) -> list[str]:
    """The most frequent remaining errors of one system."""
    lines = ["| Error | Concept / category | Text | Count |", "|---|---|---|---|"]
    for (kind, what, surface), n in s.errors.most_common(limit):
        lines.append(f"| {kind} | {what} | `{surface}` | {n:,} |")
    return lines


def llm_rows(
    run: FallbackRun, budget: tuple[float, float], notes: int, total: int, model: str
) -> list[str]:
    """What the LLM fallback did and cost, and the projection for every note."""
    spent, cap = budget
    paid = run.calls - run.cached
    input_price, output_price = price_per_token(model)
    # What these calls cost without the cache (tokens are counted for cache hits too).
    cold = run.input_tokens * input_price + run.output_tokens * output_price
    per_note = cold / notes if notes else 0.0
    reasons = ", ".join(f"{k} {v:,}" for k, v in sorted(run.reasons.items())) or "none"
    return [
        "| | |", "|---|---|",
        f"| Sentences sent (why) | {sum(run.reasons.values()):,} ({reasons}) |",
        f"| Model calls | {paid:,} paid, {run.cached:,} answered from the cache, "
        f"{run.failed:,} failed |",
        f"| Tokens | {run.input_tokens:,} in, {run.output_tokens:,} out |",
        f"| Spent by this run | ${run.cost_usd:.4f} |",
        f"| Batch total (every run of this sample) / cap | ${spent:.4f} / ${cap:.2f} |",
        f"| Without the cache, these calls cost | ${cold:.4f} (${per_note:.5f} per note) |",
        f"| Projected for all {total:,} notes | ${per_note * total:.2f} (an upper bound: repeated "
        "sentences are answered from the cache) |",
        f"| Quotes not found in the sentence (dropped) | {run.dropped_quotes:,} |",
        "| Budget reached | " + ("yes: later sentences stayed rules-only |"
                                 if run.budget_exhausted else "no |"),
    ]  # fmt: skip


LIMITS = """\
- **These notes are much easier than real ones.** One generator writes every note from a
  handful of templates (HPI, PMH, FHx, Meds, Labs, A/P), with a closed list of 38 concepts and
  a few surface forms each. Real notes have typos, copy-forward text, tables, unusual
  abbreviations, long problem lists and phrasing no rule has seen. Expect much lower numbers
  on real text; these numbers show the pipeline works, not how well it generalizes.
- **The rules were written knowing the generator.** The abbreviation list (TXLENS_NOTE_ABBREV)
  and three rules were added after reading errors on the 300 tuning notes (seed 7). The three
  rules are: PMH lists active conditions; the transplant indication and DGF are historical;
  "post-op course" is historical. They are clinically motivated, but they are tuned to this
  generator. Before them, medspaCy scored 88.0% on temporality and 94.0% on concepts.
- **Held-out is not the same as unseen text.** The held-out notes were never read while
  writing rules, so they guard against fitting individual notes. They come from the same
  templates, though, so held-out and tuning scores are close; that closeness says nothing
  about real notes.
- **The abbreviation list and the condition-text map are curated by an AI assistant and are
  pending clinical review** (TXLENS_NOTE_ABBREV, TXLENS_CONDITION and TXLENS_COND_VALUE
  rows of SOURCE_TO_CONCEPT_MAP.csv). No clinician has checked them yet.
- **The ground truth comes from the same generator,** so there is no annotation noise and no
  disagreement between annotators. Real evaluation needs clinician-annotated notes.
- **"Graft tenderness" has no SNOMED concept** in our vocabulary. The LLM finds it, but it is
  stored with concept 0 and goes to review.
- **Concept accuracy** counts a mapping as correct when it is the expected concept or one of
  its descendants: "rejection" may be any transplanted-organ rejection.
- **The LLM sees one de-identified sentence at a time.** It never sees a whole note,
  identifiers or dates. Its findings must quote the sentence, and its concept comes from our
  vocabulary."""


SPLIT_TITLES = {
    HELD_OUT: "Held-out notes (never read while writing the rules)",
    TUNING: f"Tuning notes ({SAMPLE} notes, seed {SEED}: read while writing the rules)",
}


def split_section(split: Split, title: str) -> list[str]:
    """Mentions, attributes and remaining errors of every system on one split."""
    best = list(split.scores)[-1]
    return [
        f"## {title}",
        "",
        f"{split.notes:,} notes, {split.facts:,} ground-truth facts.",
        "",
        "### Mentions found (precision, recall, F1)",
        "",
        *system_rows(split.scores),
        "",
        "### Attributes of matched mentions",
        "",
        *attribute_rows(split.scores),
        "",
        f"### Most frequent remaining errors ({best})",
        "",
        *error_rows(split.scores[best]),
        "",
    ]


def render(splits: list[Split], run: dict[str, str], llm: list[str] | None = None) -> str:
    """The report: one section per split, the first one is the headline."""
    notes = sum(s.notes for s in splits)
    seconds: Counter[str] = Counter()
    for split in splits:
        seconds.update({name: s.seconds for name, s in split.scores.items()})
    out = [
        "# Clinical-note NLP: evaluation",
        "",
        f"Generated {date.today().isoformat()} by `make notes-report` "
        "(evals/src/txlens_evals/notes_nlp.py). Synthetic data only.",
        "",
        "| Run | |", "|---|---|",
        *(f"| {k} | {v} |" for k, v in run.items()),
        f"| Notes | {notes:,} |",
        f"| Ground-truth facts | {sum(s.facts for s in splits):,} |",
        "",
        *(line for split in splits
          for line in split_section(split, SPLIT_TITLES.get(split.name, split.name.capitalize()))),
        *(["## LLM fallback (hybrid)", "", *llm, ""] if llm else []),
        "## Speed",
        "",
        *(f"- {name}: {total / max(notes, 1) * 1000:.1f} ms per note"
          for name, total in seconds.items()),
        "",
        "## Honest limits",
        "",
        LIMITS,
        "",
    ]  # fmt: skip
    return "\n".join(out)


def build_systems(engine: Engine, tenant: str) -> tuple[NoteNlp, NoteNlp]:
    """(rules-only, medspaCy), sharing one vocabulary."""
    with tenant_transaction(engine, tenant) as conn:
        vocabulary = load_vocabulary(conn)
    return NoteNlp(vocabulary, context=False), NoteNlp(vocabulary, context=True)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--sample", type=int, default=SAMPLE)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--full", action="store_true", help="every note")
    parser.add_argument("--llm", action="store_true", help="also run the hybrid (paid calls)")
    parser.add_argument("--max-cost", type=float, help="LLM spending cap in dollars (with --llm)")
    parser.add_argument("--out", type=Path, default=REPORT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Evaluate and write the report."""
    args = parse_args(argv)
    settings = Settings()
    engine = make_engine(settings.app_database_url.get_secret_value())
    facts = read_facts(args.data / "ground_truth" / "note_facts.jsonl")
    tenants_of = note_tenants(args.data)
    chosen = choose(tenants_of, None if args.full else args.sample, args.seed)
    tuning = {i for ids in choose(tenants_of, SAMPLE, SEED).values() for i in ids}
    groups = split_of(chosen, tuning) if args.full else {"sample": chosen}
    rules, medspacy = build_systems(engine, sorted(chosen)[0])
    systems: dict[str, System] = {"rules-only": rules_system(rules),
                                  "medspaCy": rules_system(medspacy)}  # fmt: skip
    run = {"Sample": "all notes" if args.full else f"{args.sample} notes, seed {args.seed}"}
    fallback = FallbackRun()
    budget = None
    if args.llm:
        if args.max_cost is None:
            raise SystemExit("--llm needs --max-cost (the spending cap in dollars)")
        batch = "eval-notes-full" if args.full else f"eval-notes-{args.sample}-{args.seed}"
        budget = Budget(engine, batch, min(settings.llm_budget_usd, args.max_cost))
        budget.open()
        systems["hybrid (+LLM)"] = hybrid_system(medspacy, engine, settings, budget, fallback)
        run |= {"Model": settings.llm_model, "Prompt": PROMPT.version, "Batch": batch}
    splits = [evaluate(engine, ids, facts, systems, name) for name, ids in groups.items()]
    notes = sum(s.notes for s in splits)
    llm = None
    if budget is not None:
        spent, cap = budget.totals()
        llm = llm_rows(
            fallback, (float(spent), float(cap)), notes, len(tenants_of), settings.llm_model
        )
    report = render(splits, run, llm)
    args.out.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
