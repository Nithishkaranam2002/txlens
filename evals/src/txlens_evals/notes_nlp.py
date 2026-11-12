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
import hashlib
import json
import random
import re
import statistics
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
from txlens_backend.documents.passes import guarded_pass
from txlens_backend.notes.fallback import (
    PROMPT,
    Fallback,
    FallbackRun,
    Sentence,
    SentenceFindings,
    document,
)
from txlens_backend.notes.nlp import Mention, NoteNlp
from txlens_backend.notes.results import needs_review
from txlens_backend.notes.store import NoteRow, phi_spans, read_notes
from txlens_backend.notes.vocabulary import load_vocabulary
from txlens_evals.stats import Estimate, bootstrap, f1, ratio, write_headline
from txlens_pipelines.deid_notes import Replacement, to_original_offset

CATEGORIES = ("condition", "medication", "lab", "procedure")
SAMPLE = 300
SEED = 7  # the 300 notes of seed 7 were read while writing the rules: the tuning notes
HELD_OUT = "held-out"
TUNING = "tuning"
LLM_SAMPLE = "held-out sample"  # where all four systems, LLM only included, are compared
LLM_ONLY_SEED = 11
LLM_ONLY = "LLM only"
LLM_ONLY_WORKERS = 8  # parallel calls per note (every sentence is asked)
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
    "graft_tenderness": (("TxLens", "GRAFT_TENDERNESS"),),
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
# Per-note counts kept for the bootstrap: mentions found (tp, fp, fn), attributes right on
# matched facts, matched facts, and mentions sent to review out of all mentions.
UNIT_COLUMNS = ("tp", "fp", "fn", "negation", "temporality", "experiencer", "concept",
                "matched", "review", "mentions")  # fmt: skip
REVIEW_THRESHOLD = 0.70


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
    # Mentions a rule sent to review, by the ground truth's temporality of the matched fact
    review_flagged: Counter[str] = field(default_factory=Counter)
    # (error, concept, de-identified sentence) of attribute errors, kept only for --dump-errors
    examples: list[tuple[str, str, str]] | None = None
    # One row per note for bootstrap intervals: see UNIT_COLUMNS
    units: list[tuple[int, ...]] = field(default_factory=list)
    # Ground-truth concept key -> tp / fp / fn (a false positive counts for the concept its
    # vocabulary concept stands for, else "other")
    per_concept: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))


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
    expected: dict[str, frozenset[int]], deid_text: str = "",
) -> None:  # fmt: skip
    """Add one note's matches and errors to `scores`. Attribute errors name their direction,
    e.g. "wrong temporality (historical→current)": expected, then predicted."""
    left = list(facts)
    review = sum(needs_review(m, REVIEW_THRESHOLD) for m in mentions)
    unit: Counter[str] = Counter(mentions=len(mentions), review=review)
    for m in mentions:
        start, end = original_span(m, spans)
        fact = next(
            (f for f in left if f.category == m.category and f.start < end and start < f.end),
            None,
        )
        if fact is None:
            scores.fp[m.category] += 1
            unit["fp"] += 1
            scores.per_concept[concept_key(m.concept_id, expected)]["fp"] += 1
            if any(s < end and start < e for s, e in untracked):
                scores.fp_untracked[m.category] += 1
            else:
                scores.errors[("false positive", m.category, m.text.lower())] += 1
            continue
        left.remove(fact)
        if m.review_reason is not None:
            scores.review_flagged[fact.temporality] += 1
        scores.tp[m.category] += 1
        scores.matched += 1
        unit["tp"] += 1
        unit["matched"] += 1
        scores.per_concept[fact.concept]["tp"] += 1
        checks = {
            "negation": (m.negated == fact.negated, f"{fact.negated}→{m.negated}"),
            "temporality": (m.temporality == fact.temporality,
                            f"{fact.temporality}→{m.temporality}"),
            "experiencer": (m.experiencer == fact.experiencer,
                            f"{fact.experiencer}→{m.experiencer}"),
            "concept": (m.concept_id in expected.get(fact.concept, frozenset()), ""),
        }  # fmt: skip
        for name, (right, direction) in checks.items():
            scores.attribute_right[name] += right
            unit[name] += right
            if not right:
                kind = f"wrong {name} ({direction})" if direction else f"wrong {name}"
                scores.errors[(kind, fact.concept, fact.text.lower())] += 1
                if scores.examples is not None:
                    sentence = deid_text[m.sentence_start : m.sentence_end]
                    scores.examples.append((kind, fact.concept, " ".join(sentence.split())))
    for fact in left:
        scores.fn[fact.category] += 1
        unit["fn"] += 1
        scores.per_concept[fact.concept]["fn"] += 1
        scores.errors[("missed", fact.category, fact.text.lower())] += 1
    scores.units.append(tuple(unit[c] for c in UNIT_COLUMNS))


def concept_key(concept_id: int, expected: dict[str, frozenset[int]]) -> str:
    """The ground-truth concept key a predicted concept stands for, or "other"."""
    return next((k for k, ids in expected.items() if concept_id and concept_id in ids), "other")


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


def llm_only_system(nlp: NoteNlp, engine: Engine, settings: Settings, budget: Budget,
                    run: FallbackRun) -> System:  # fmt: skip
    """The LLM reads every body sentence; no rule finds anything. Each answer is still
    checked (quotes must be in the sentence) and the concept still comes from our
    vocabulary, so this measures the model, not a looser pipeline."""
    extractor = make_extractor(settings, "a", PROMPT)

    def extract(tenant: str, notes: list[NoteRow]) -> list[list[Mention]]:
        fallback = Fallback(engine, tenant, extractor, budget, nlp.vocabulary, run,
                            workers=LLM_ONLY_WORKERS)  # fmt: skip
        results = []
        for note in notes:
            asked = [Sentence(a, b, section, "every_sentence")
                     for a, b, section in nlp.sentences(note.text)]  # fmt: skip
            results.append(fallback.ask(note.text, note.organ, asked, []))
        return results

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
    systems: dict[str, System], name: str, keep_examples: bool = False,
) -> Split:  # fmt: skip
    """Run every system on the chosen notes (keeping error sentences if asked)."""
    split = Split(name, {system: Scores(examples=[] if keep_examples else None)
                         for system in systems})  # fmt: skip
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
                           untracked_spans(original), expected, note.text)  # fmt: skip
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


@dataclass(frozen=True)
class SystemCost:
    """What a system's LLM calls cost and took on one split (None for rules)."""

    run: FallbackRun | None
    model: str


def cold_cost(run: FallbackRun, model: str) -> float:
    """What the run's calls cost without the cache."""
    price_in, price_out = price_per_token(model)
    return run.input_tokens * price_in + run.output_tokens * price_out


def headline(s: Scores, notes: int, cost: SystemCost | None, call_ms: float | None
             ) -> dict[str, object]:  # fmt: skip
    """Mentions F1 and attribute accuracy with 95% intervals, review share, cost, latency."""
    col = {c: i for i, c in enumerate(UNIT_COLUMNS)}
    out: dict[str, object] = {
        "f1": bootstrap(s.units, f1),
        **{name: bootstrap(s.units, ratio(col[name], col["matched"]))
           for name in ("negation", "temporality", "experiencer", "concept")},
        "review_share": bootstrap(s.units, ratio(col["review"], col["mentions"])),
    }  # fmt: skip
    run = cost.run if cost else None
    calls = run.calls if run else 0
    out["cost_per_note"] = cold_cost(run, cost.model) / notes if run and cost and notes else 0.0
    measured_ms = s.seconds / max(notes, 1) * 1000
    out["latency_ms_per_note"] = measured_ms + (calls / max(notes, 1)) * (call_ms or 0.0)
    return out


def headline_rows(split: Split, costs: dict[str, SystemCost], call_ms: float | None
                  ) -> list[str]:  # fmt: skip
    """One row per system: 95% bootstrap intervals over notes."""
    lines = ["| System | F1 (mentions) | Negation | Temporality | Concept | Sent to review | "
             "LLM cost / note | Latency / note |", "|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for name, scores in split.scores.items():
        h = headline(scores, split.notes, costs.get(name), call_ms)
        estimates = [h[k] for k in ("f1", "negation", "temporality", "concept", "review_share")]
        latency = float(h["latency_ms_per_note"])  # type: ignore[arg-type]
        lines.append(
            f"| {name} | "
            + " | ".join(e.text() for e in estimates)  # type: ignore[attr-defined]
            + f" | ${float(h['cost_per_note']):.5f} | "  # type: ignore[arg-type]
            + (f"{latency / 1000:.1f} s" if latency >= 1000 else f"{latency:.0f} ms")
            + " |"
        )
    return lines


def concept_rows(split: Split) -> list[str]:
    """F1 per ground-truth concept, one column per system (point estimates)."""
    names = list(split.scores)
    keys = sorted({k for s in split.scores.values() for k in s.per_concept})
    lines = ["| Concept | " + " | ".join(f"{n} F1" for n in names) + " |",
             "|---|" + "---|" * len(names)]  # fmt: skip
    for key in keys:
        cells = []
        for n in names:
            c = split.scores[n].per_concept.get(key, Counter())
            cells.append(pct(prf(c["tp"], c["fp"], c["fn"])[2]) if sum(c.values()) else "-")
        lines.append(f"| {key} | " + " | ".join(cells) + " |")
    return lines


def review_rows(scores: dict[str, Scores]) -> list[str]:
    """Rejection mentions the past-rejection rule sent to a person, by the true temporality."""
    lines = ["| System | Sent to review | Truly historical | Truly current |", "|---|---|---|---|"]
    for name, s in scores.items():
        flagged = s.review_flagged
        lines.append(f"| {name} | {sum(flagged.values()):,} | {flagged['historical']:,} | "
                     f"{flagged['current']:,} |")  # fmt: skip
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
  Step 2.3 added two more after reading the tuning errors: ", treated" closes a past episode,
  and "will arrange" is a plan (hypothetical). Before them, held-out temporality was 99.8%,
  with ~285 past rejections read as current.
- **A safety net, not a guess.** A rejection the rules still call current, in a sentence with
  a date before the date of service or a treatment cue, goes to the review queue instead of
  into cohorts. It does not depend on the generator's wording.
- **Held-out is not the same as unseen text.** The held-out notes were never read while
  writing rules, so they guard against fitting individual notes. They come from the same
  templates, though, so held-out and tuning scores are close; that closeness says nothing
  about real notes.
- **The abbreviation list and the condition-text map are curated by an AI assistant and are
  pending clinical review** (TXLENS_NOTE_ABBREV, TXLENS_CONDITION and TXLENS_COND_VALUE
  rows of SOURCE_TO_CONCEPT_MAP.csv). No clinician has checked them yet.
- **The ground truth comes from the same generator,** so there is no annotation noise and no
  disagreement between annotators. Real evaluation needs clinician-annotated notes.
- **"Graft tenderness" has no standard concept.** SNOMED has only "Tenderness of kidney" (any
  kidney, Observation domain). Since Step 2.3 the term maps to a TxLens local concept
  (GRAFT_TENDERNESS, curated by AI assistant, pending clinical review); before that the LLM
  found it with concept 0 and every mention went to review.
- **Concept accuracy** counts a mapping as correct when it is the expected concept or one of
  its descendants: "rejection" may be any transplanted-organ rejection.
- **The LLM sees one de-identified sentence at a time.** It never sees a whole note,
  identifiers or dates. Its findings must quote the sentence, and its concept comes from our
  vocabulary."""


SPLIT_TITLES = {
    LLM_SAMPLE: "Four systems on a held-out sample (300 notes, seed 11)",
    HELD_OUT: "Held-out notes (never read while writing the rules)",
    TUNING: f"Tuning notes ({SAMPLE} notes, seed {SEED}: read while writing the rules)",
}


def split_section(
    split: Split, title: str, costs: dict[str, SystemCost] | None = None,
    call_ms: float | None = None,
) -> list[str]:  # fmt: skip
    """Headline with intervals, mentions, attributes and remaining errors on one split."""
    best = list(split.scores)[-1]
    per_concept = ["### F1 per concept", "", *concept_rows(split), ""] \
        if split.name == LLM_SAMPLE else []  # fmt: skip
    return [
        f"## {title}",
        "",
        f"{split.notes:,} notes, {split.facts:,} ground-truth facts.",
        "",
        "### Headline (95% bootstrap intervals, notes resampled 1,000 times)",
        "",
        *headline_rows(split, costs or {}, call_ms),
        "",
        "Accuracy of negation, temporality and concept is over the matched facts. Sent to "
        "review: mentions the pipeline would send to a person (no concept, ambiguous, or "
        "confidence below 0.70: every LLM finding). Cost is without the cache; latency is "
        "measured, plus the median paid-call time for each call answered from the cache.",
        "",
        *per_concept,
        "### Mentions found (precision, recall, F1)",
        "",
        *system_rows(split.scores),
        "",
        "### Attributes of matched mentions",
        "",
        *attribute_rows(split.scores),
        "",
        "### Rejections called current but dated earlier or treated (sent to review)",
        "",
        *review_rows(split.scores),
        "",
        f"### Most frequent remaining errors ({best})",
        "",
        *error_rows(split.scores[best]),
        "",
    ]


def render(
    splits: list[Split], run: dict[str, str], llm: list[str] | None = None,
    costs: dict[str, dict[str, SystemCost]] | None = None, call_ms: float | None = None,
) -> str:  # fmt: skip
    """The report: one section per split, the first one is the headline. Totals and speed
    count each note once (the LLM-only sample's notes are also in the held-out split)."""
    counted = [s for s in splits if s.name != LLM_SAMPLE]
    notes = sum(s.notes for s in counted)
    seconds: Counter[str] = Counter()
    for split in counted:
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
        f"| Ground-truth facts | {sum(s.facts for s in counted):,} |",
        "",
        *(line for split in splits
          for line in split_section(split, SPLIT_TITLES.get(split.name, split.name.capitalize()),
                                    (costs or {}).get(split.name), call_ms)),
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
    parser.add_argument(
        "--llm-only-sample",
        type=int,
        default=0,
        metavar="N",
        help="with --full --llm: also run the LLM on every sentence of N "
        "held-out notes, and compare all four systems there",
    )
    parser.add_argument(
        "--measure-latency",
        type=int,
        default=0,
        metavar="N",
        help="with --full --llm: time N uncached model calls (about "
        "N x $0.0001) and record the median",
    )
    parser.add_argument("--llm-only-cost", type=float, default=1.0,
                        help="spending cap in dollars for the LLM-only run")  # fmt: skip
    parser.add_argument(
        "--dump-errors", type=Path, metavar="FILE",
        help="write the de-identified sentences of attribute errors on the tuning notes only "
        "(never held-out) to FILE, as JSON lines",
    )  # fmt: skip
    return parser.parse_args(argv)


def dump_errors(splits: list[Split], path: Path) -> None:
    """Attribute-error sentences of the tuning notes (held-out notes are never dumped)."""
    with path.open("w", encoding="utf-8") as handle:
        for split in splits:
            if split.name not in (TUNING, "sample"):
                continue
            for system, scores in split.scores.items():
                for kind, concept, sentence in scores.examples or []:
                    row = {"system": system, "error": kind, "concept": concept,
                           "sentence": sentence}  # fmt: skip
                    handle.write(json.dumps(row) + "\n")


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
    model = settings.llm_model
    costs = {name: {"hybrid (+LLM)": SystemCost(fallback, model)} for name in groups}
    splits: list[Split] = []
    paid_ms: list[int] = []
    only_rows: list[str] = []
    if args.llm_only_sample and budget is not None and HELD_OUT in groups:
        # First, so a problem in the paid part shows before the long free part runs.
        sample = held_out_sample(groups[HELD_OUT], args.llm_only_sample)
        only_run, sample_hybrid = FallbackRun(), FallbackRun()
        batch_name = f"eval-notes-llm-only-{args.llm_only_sample}-{LLM_ONLY_SEED}"
        only_budget = Budget(engine, batch_name, min(settings.llm_budget_usd, args.llm_only_cost))
        only_budget.open()
        hybrid = hybrid_system(medspacy, engine, settings, budget, sample_hybrid)
        only = llm_only_system(medspacy, engine, settings, only_budget, only_run)
        four = {"rules-only": systems["rules-only"], "medspaCy": systems["medspaCy"],
                "hybrid (+LLM)": hybrid, LLM_ONLY: only}  # fmt: skip
        splits.append(evaluate(engine, sample, facts, four, LLM_SAMPLE))
        costs[LLM_SAMPLE] = {"hybrid (+LLM)": SystemCost(sample_hybrid, model),
                             LLM_ONLY: SystemCost(only_run, model)}  # fmt: skip
        paid_ms += only_run.paid_latency_ms
        spent, cap = only_budget.totals()
        only_rows = ["", f"LLM only, {args.llm_only_sample} held-out notes:", "",
                     *llm_rows(only_run, (float(spent), float(cap)), args.llm_only_sample,
                               len(tenants_of), model)]  # fmt: skip
    splits += [
        evaluate(engine, ids, facts, systems, name,
                 keep_examples=args.dump_errors is not None and name != HELD_OUT)
        for name, ids in groups.items()
    ]  # fmt: skip
    if args.dump_errors is not None:
        dump_errors(splits, args.dump_errors)
    notes = sum(s.notes for s in splits if s.name != LLM_SAMPLE)
    llm = None
    if budget is not None:
        spent, cap = budget.totals()
        llm = [*llm_rows(fallback, (float(spent), float(cap)), notes, len(tenants_of), model),
               *only_rows]  # fmt: skip
    paid_ms += fallback.paid_latency_ms
    if args.measure_latency and budget is not None:
        paid_ms = measure_call_latency(engine, settings, budget, medspacy, groups,
                                       args.measure_latency)  # fmt: skip
    call_ms = call_latency(paid_ms, model, args.out.parent / LATENCY_FILE)
    report = render(splits, run, llm, costs, call_ms)
    args.out.write_text(report, encoding="utf-8")
    write_headline(args.out.with_suffix(".json"), headline_json(splits, costs, call_ms))
    print(report)
    return 0


LATENCY_FILE = "llm_call_latency.json"


def call_latency(paid_ms: list[int], model: str, path: Path) -> float | None:
    """The median time of one model call. A run with paid calls measures it and records it;
    a fully cached run (no calls to time) reads the recorded measurement instead."""
    key = f"{model}|{PROMPT.version}"
    recorded = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if paid_ms:
        recorded[key] = {"median_ms": statistics.median(paid_ms), "calls": len(paid_ms),
                         "measured": date.today().isoformat()}  # fmt: skip
        path.write_text(json.dumps(recorded, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    entry = recorded.get(key)
    return float(entry["median_ms"]) if entry else None


def measure_call_latency(
    engine: Engine, settings: Settings, budget: Budget, nlp: NoteNlp,
    groups: dict[str, dict[str, list[str]]], n: int,
) -> list[int]:  # fmt: skip
    """Time n real model calls on held-out sentences, bypassing the cache (a fresh cache key
    per call), one at a time. Costs about n x $0.0001."""
    extractor = make_extractor(settings, "a", PROMPT)
    sample = held_out_sample(groups[HELD_OUT], n)
    times: list[int] = []
    for tenant, ids in sorted(sample.items()):
        with tenant_transaction(engine, tenant) as conn:
            notes = read_notes(conn, ids)
        for note in notes:
            start, end, section = nlp.sentences(note.text)[0]
            block = document(note.text[start:end], section)
            salt = hashlib.sha256(f"latency-probe|{date.today()}|{note.source_value}".encode())
            outcome = guarded_pass(engine, tenant, extractor, budget, SentenceFindings, block,
                                   "a", salt.hexdigest())  # fmt: skip
            if not outcome.cached and outcome.error is None:
                times.append(outcome.latency_ms)
    return times


def held_out_sample(held_out: dict[str, list[str]], n: int) -> dict[str, list[str]]:
    """n held-out notes (never the tuning notes), the same ones on every run."""
    pool = sorted(i for ids in held_out.values() for i in ids)
    tenant = {i: t for t, ids in held_out.items() for i in ids}
    by_tenant: dict[str, list[str]] = defaultdict(list)
    for note_id in sorted(random.Random(LLM_ONLY_SEED).sample(pool, min(n, len(pool)))):
        by_tenant[tenant[note_id]].append(note_id)
    return dict(by_tenant)


def headline_json(
    splits: list[Split], costs: dict[str, dict[str, SystemCost]], call_ms: float | None
) -> dict[str, object]:
    """Every split's headline metrics, for summary.md."""
    out: dict[str, object] = {}
    for split in splits:
        systems = {}
        for name, scores in split.scores.items():
            h = headline(scores, split.notes, costs.get(split.name, {}).get(name), call_ms)
            systems[name] = {k: (v.as_dict() if isinstance(v, Estimate) else v)
                             for k, v in h.items()}  # fmt: skip
        out[split.name] = {"notes": split.notes, "facts": split.facts, "systems": systems}
    return out


if __name__ == "__main__":
    raise SystemExit(main())
