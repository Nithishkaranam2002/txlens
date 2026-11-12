"""medspaCy rules over a de-identified note: which concepts are mentioned, and how.

Pipeline (blank English, no model download):
1. PyRuSH sentence splitter;
2. TargetMatcher with one token pattern per vocabulary term (vocabulary.py), longest match
   wins;
3. ConText (negation, historical, hypothetical, family) with the default rules plus a few
   transplant-note phrases;
4. Sectionizer with the default rules plus these notes' headers (PMH, FHx, Meds, A/P). Only
   the family-history section sets an attribute (experiencer = family). medspaCy's default
   also calls everything under a past-medical-history header historical; in a
   post-transplant clinic note that list holds active comorbidities (hypertension, diabetes),
   so history comes from cue words (h/o, s/p, resolved, prior) instead.

Two transplant rules run after ConText:
- a term whose own words start with a history cue ("prior Tx") is historical;
- the transplant indication (end-stage renal disease, end-stage heart failure) and delayed
  graft function (a first-week event) are historical in a post-transplant note.

The note header (title, center, patient line, date) is metadata, not clinical text: mentions
before the first blank line are dropped.

`context=False` keeps only steps 1-2 and calls every mention affirmed, current and about the
patient: the "rules-only" baseline of the evaluation.
"""

from dataclasses import dataclass, replace
from typing import Literal

import medspacy
from loguru import logger
from medspacy.context import ConTextRule
from medspacy.ner import TargetRule
from medspacy.section_detection import SectionRule
from spacy.language import Language
from spacy.tokens import Doc, Span

from txlens_backend.notes.vocabulary import Category, Vocabulary

Temporality = Literal["current", "historical", "hypothetical"]
Experiencer = Literal["patient", "family"]
SYSTEM_RULES = "txlens-medspacy-1"
SYSTEM_BASELINE = "txlens-rules-1"
FAMILY_SECTION = "family_history"

logger.disable("PyRuSH")  # its debug log prints every token

SECTIONS = [
    SectionRule("HPI:", "history_of_present_illness"),
    SectionRule("PMH:", "past_medical_history"),
    SectionRule("FHx:", FAMILY_SECTION),
    SectionRule("Meds:", "medications"),
    SectionRule("Labs:", "labs_and_studies"),
    SectionRule("A/P:", "observation_and_plan"),
    SectionRule("Indication:", "indication"),
    SectionRule("Result:", "labs_and_studies"),
    SectionRule("Plan:", "observation_and_plan"),
]
# Transplant-note phrasing the default ConText rules do not cover.
CONTEXT = [
    ConTextRule("without", "NEGATED_EXISTENCE", direction="FORWARD"),
    ConTextRule("no evidence of", "NEGATED_EXISTENCE", direction="FORWARD"),
    ConTextRule("not detected", "NEGATED_EXISTENCE", direction="BACKWARD"),
    ConTextRule("s/p", "HISTORICAL", direction="FORWARD"),
    ConTextRule("h/o", "HISTORICAL", direction="FORWARD"),
    ConTextRule("prior", "HISTORICAL", direction="FORWARD"),
    ConTextRule("resolved", "HISTORICAL", direction="BACKWARD"),
    ConTextRule("signs of", "HYPOTHETICAL", direction="FORWARD"),
    ConTextRule("watch for", "HYPOTHETICAL", direction="BACKWARD"),
    ConTextRule("will plan", "HYPOTHETICAL", direction="FORWARD"),
    ConTextRule("post-op course", "HISTORICAL", direction="FORWARD"),
]
HISTORY_CUES = ("prior ", "h/o ", "s/p ")
# End-stage renal disease, heart failure (transplant indications), delayed renal graft
# function: in a note written after the transplant these describe the past.
BEFORE_FOLLOW_UP = frozenset({193782, 316139, 4138908})


@dataclass(frozen=True)
class Mention:
    """One concept mention, with offsets into the (de-identified) note text."""

    start: int
    end: int
    text: str
    category: Category
    concept_id: int  # 0 when the term is ambiguous
    negated: bool
    temporality: Temporality
    experiencer: Experiencer
    section: str | None
    sentence_start: int
    sentence_end: int
    method: str  # "curated", "vocabulary", "ambiguous" (and "llm" for the fallback)
    confidence: float
    conflict: bool = False  # ConText says both hypothetical and historical


class NoteNlp:
    """A medspaCy pipeline built for one vocabulary."""

    def __init__(self, vocabulary: Vocabulary, *, context: bool = True) -> None:
        self.vocabulary = vocabulary
        self.context = context
        enable = ["medspacy_pyrush", "medspacy_target_matcher"]
        if context:
            enable.append("medspacy_context")
        self.nlp: Language = medspacy.load(medspacy_enable=enable)
        self.nlp.get_pipe("medspacy_target_matcher").add(self.target_rules())
        if context:
            self.nlp.get_pipe("medspacy_context").add(CONTEXT)
            sectionizer = self.nlp.add_pipe(
                "medspacy_sectionizer",
                config={"span_attrs": {FAMILY_SECTION: {"is_family": True}}},
            )
            sectionizer.add(SECTIONS)

    @property
    def system(self) -> str:
        """The nlp_system name written to note_nlp."""
        return SYSTEM_RULES if self.context else SYSTEM_BASELINE

    def target_rules(self) -> list[TargetRule]:
        """Case-insensitive token patterns per term. The tokenizer splits by case ("t2dm" is
        two tokens, "T2DM" one), so the lower, upper and title forms each get a pattern."""
        rules = []
        for surface in sorted(self.vocabulary.texts()):
            variants = {surface, surface.upper(), surface.title()}
            patterns = {tuple(t.lower_ for t in self.nlp.tokenizer(v)) for v in variants}
            for tokens in sorted(patterns):
                rules.append(TargetRule(surface, "TERM", pattern=[{"LOWER": t} for t in tokens]))
        return rules

    def extract(self, text: str, organ: str | None) -> list[Mention]:
        """Mentions in one note (organ picks organ-specific terms such as 'kidney:tcmr')."""
        return self.mentions(self.nlp(text), organ)

    def extract_many(self, notes: list[tuple[str, str | None]]) -> list[list[Mention]]:
        """Mentions for several (text, organ) notes, processed as a stream."""
        docs = self.nlp.pipe(text for text, _ in notes)
        return [self.mentions(doc, organ) for doc, (_, organ) in zip(docs, notes, strict=True)]

    def mentions(self, doc: Doc, organ: str | None) -> list[Mention]:
        """Each entity of a processed doc after the header, as a Mention."""
        body = doc.text.find("\n\n")
        return [
            m
            for ent in doc.ents
            if ent.start_char > body and (m := self.mention(ent, organ)) is not None
        ]

    def mention(self, ent: Span, organ: str | None) -> Mention | None:
        """One entity; None if its text is not in the vocabulary (cannot happen for rules)."""
        surface = " ".join(ent.text.lower().split())
        term = self.vocabulary.lookup(surface, organ)
        if term is None and surface not in self.vocabulary.ambiguous:
            return None
        sentence = ent.sent
        base = Mention(
            start=ent.start_char,
            end=ent.end_char,
            text=ent.text,
            category=term.category if term else "condition",
            concept_id=term.concept_id if term else 0,
            negated=False,
            temporality="current",
            experiencer="patient",
            section=None,
            sentence_start=sentence.start_char,
            sentence_end=sentence.end_char,
            method=("curated" if term.curated else "vocabulary") if term else "ambiguous",
            confidence=1.0 if term else 0.0,
        )
        if not self.context:
            return base
        section = ent._.section_category
        if ent._.is_hypothetical:
            temporality: Temporality = "hypothetical"
        elif (ent._.is_historical or base.concept_id in BEFORE_FOLLOW_UP
              or surface.startswith(HISTORY_CUES)):  # fmt: skip
            temporality = "historical"
        else:
            temporality = "current"
        family = ent._.is_family or section == FAMILY_SECTION
        return replace(
            base,
            negated=bool(ent._.is_negated),
            temporality=temporality,
            experiencer="family" if family else "patient",
            section=section,
            conflict=bool(ent._.is_hypothetical and ent._.is_historical),
        )
