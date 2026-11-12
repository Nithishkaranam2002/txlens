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

A rejection read as current when it was in the past puts a patient in "current rejection"
cohorts, so one more check runs on rejection mentions the rules call current: if the
sentence also holds a date before the date of service or a treatment cue, the mention gets
a review reason and a person decides (the rules do not guess between a recent and an old
episode).

The note header (title, center, patient line, date) is metadata, not clinical text: mentions
before the first blank line are dropped.

`context=False` keeps only steps 1-2 and calls every mention affirmed, current and about the
patient: the "rules-only" baseline of the evaluation.
"""

import re
from dataclasses import dataclass, replace
from datetime import date, datetime
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
SYSTEM_RULES = "txlens-medspacy-2"  # 2: past-rejection rules (Step 2.3)
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
    # ", treated." closes a finished episode ("Bx 3/22/24 showed ABMR, treated."); an
    # episode still being treated is current.
    ConTextRule("treated", "HISTORICAL", direction="BACKWARD"),
    ConTextRule("being treated", "PSEUDO", direction="PSEUDO"),
    ConTextRule("to be treated", "PSEUDO", direction="PSEUDO"),
    ConTextRule("signs of", "HYPOTHETICAL", direction="FORWARD"),
    ConTextRule("watch for", "HYPOTHETICAL", direction="BACKWARD"),
    ConTextRule("will plan", "HYPOTHETICAL", direction="FORWARD"),
    ConTextRule("will arrange", "HYPOTHETICAL", direction="FORWARD"),
    ConTextRule("will schedule", "HYPOTHETICAL", direction="FORWARD"),
    ConTextRule("post-op course", "HISTORICAL", direction="FORWARD"),
]
HISTORY_CUES = ("prior ", "h/o ", "s/p ")
# End-stage renal disease, heart failure (transplant indications), delayed renal graft
# function: in a note written after the transplant these describe the past.
BEFORE_FOLLOW_UP = frozenset({193782, 316139, 4138908})
REVIEW_PAST_REJECTION = "rejection called current, but dated earlier or treated"
SERVICE_DATE = re.compile(r"Date of service:\s*(\S+)")
DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2}(?:\d{2})?)\b")
DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y")
TREATMENT_CUE = re.compile(r"\b(treated|s/p|status post|resolved|h/o|history of)\b", re.I)


def parse_date(value: str) -> date | None:
    """A note date in one of the formats clinicians write; None if it is none of them."""
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def service_date(note_text: str) -> date | None:
    """The note's date of service from its header, if it has one."""
    found = SERVICE_DATE.search(note_text)
    return parse_date(found.group(1)) if found else None


def past_episode_cue(sentence: str, service: date | None) -> bool:
    """True if the sentence names a date before the date of service or a treatment cue."""
    if TREATMENT_CUE.search(sentence):
        return True
    dates = (parse_date(d) for d in DATE.findall(sentence))
    return service is not None and any(d is not None and d < service for d in dates)


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
    review_reason: str | None = None  # a rule asks for a person to check this mention


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
        service = service_date(doc.text[: body if body > 0 else None])
        return [
            m
            for ent in doc.ents
            if ent.start_char > body and (m := self.mention(ent, organ, service)) is not None
        ]

    def mention(self, ent: Span, organ: str | None, service: date | None = None) -> Mention | None:
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
        negated = bool(ent._.is_negated)
        past_rejection = (
            base.concept_id in self.vocabulary.rejection
            and temporality == "current"
            and not negated
            and past_episode_cue(sentence.text, service)
        )
        return replace(
            base,
            negated=negated,
            temporality=temporality,
            experiencer="family" if family else "patient",
            section=section,
            conflict=bool(ent._.is_hypothetical and ent._.is_historical),
            review_reason=REVIEW_PAST_REJECTION if past_rejection else None,
        )
