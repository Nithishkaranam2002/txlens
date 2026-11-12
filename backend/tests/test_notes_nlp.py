"""Note NLP: vocabulary rules, medspaCy attributes, LLM-fallback triggers and checks (no DB)."""

from functools import cache

import pytest

from txlens_backend.notes.fallback import (
    FallbackRun,
    Finding,
    Sentence,
    SentenceFindings,
    document,
    sentences_to_ask,
    to_mentions,
)
from txlens_backend.notes.nlp import Mention, NoteNlp
from txlens_backend.notes.vocabulary import Term, Vocabulary, build, clean, split_organ

HTN, T2DM, ESRD, EDEMA, CMV, RENAL_TCMR, CARDIAC_ACR = (
    316866,
    201826,
    193782,
    433595,
    440032,
    4128371,
    4202285,
)
CURATED = [
    Term("htn", HTN, "condition", True),
    Term("t2dm", T2DM, "condition", True),
    Term("tcmr", RENAL_TCMR, "condition", True, "kidney"),
    Term("tcmr", CARDIAC_ACR, "condition", True, "heart"),
    Term("cr", 3016723, "lab", True),
    Term("dd-cfdna", 3966008, "lab", True),
    Term("ktx", 4322471, "procedure", True),
    Term("tacro", 950637, "medication", True),
]
AUTOMATIC = [
    Term("hypertension", HTN, "condition", False),
    Term("end-stage renal disease", ESRD, "condition", False),
    Term("edema", EDEMA, "condition", False),
    Term("cmv infection", CMV, "condition", False),
]
NOTE = """TRANSPLANT CLINIC NOTE
Patient: Hollis, Maren    HTN clinic
Date of service: 2025-01-04

HPI: 44 y/o M s/p KTx. Denies edema.
PMH:
- end-stage renal disease 2/2 lupus nephritis
- hypertension
- h/o CMV infection, resolved
FHx: Mother with T2DM
Labs:
- Cr 1.69 (01/03)
A/P: TCMR on biopsy. If dd-cfDNA rises above 1%, repeat. Continue tacro.
"""


@cache
def vocabulary() -> Vocabulary:
    return build(CURATED, AUTOMATIC)


@cache
def pipeline(context: bool = True) -> NoteNlp:
    return NoteNlp(vocabulary(), context=context)


def found(organ: str | None = "kidney", context: bool = True) -> dict[str, Mention]:
    return {m.text: m for m in pipeline(context).extract(NOTE, organ)}


def test_clean_and_organ_prefix() -> None:
    assert clean("Graft rejection (finding)") == "graft rejection"
    assert split_organ("kidney:tcmr") == ("kidney", "tcmr")
    assert split_organ("mixed tcmr/abmr") == (None, "mixed tcmr/abmr")


def test_curated_term_wins_and_two_automatic_concepts_are_ambiguous() -> None:
    v = build(
        [Term("pkd", 1, "condition", True)],
        [Term("pkd", 2, "condition", False), Term("cad", 3, "condition", False),
         Term("cad", 4, "condition", False)],
    )  # fmt: skip
    assert v.lookup("PKD", None) == Term("pkd", 1, "condition", True)
    assert v.lookup("cad", None) is None and "cad" in v.ambiguous


def test_header_is_not_clinical_text() -> None:
    assert [m.start for m in pipeline().extract(NOTE, "kidney") if m.text == "HTN"] == []


def test_negation_family_history_and_hypothetical() -> None:
    m = found()
    assert m["edema"].negated
    assert m["T2DM"].experiencer == "family" and m["T2DM"].concept_id == T2DM  # upper case
    assert m["CMV infection"].temporality == "historical"
    assert m["dd-cfDNA"].temporality == "hypothetical"
    assert m["KTx"].temporality == "historical"  # s/p


def test_past_medical_history_lists_active_conditions() -> None:
    m = found()
    assert m["hypertension"].temporality == "current"
    assert m["end-stage renal disease"].temporality == "historical"  # transplant indication


def test_organ_specific_terms() -> None:
    assert found("kidney")["TCMR"].concept_id == RENAL_TCMR
    assert found("heart")["TCMR"].concept_id == CARDIAC_ACR
    assert "TCMR" not in found(None)  # no organ, no general term


def test_rules_only_baseline_affirms_everything() -> None:
    m = found(context=False)
    assert not m["edema"].negated and m["T2DM"].experiencer == "patient"
    assert pipeline(False).system == "txlens-rules-1"


def mention(text: str, start: int, end: int, s_start: int, s_end: int) -> Mention:
    return Mention(start, end, text[start:end], "condition", EDEMA, True, "current", "patient",
                   "history_of_present_illness", s_start, s_end, "vocabulary", 1.0)  # fmt: skip


def test_fallback_asks_for_cue_lists_with_an_unmatched_item_only() -> None:
    text = "HDR\n\nHPI: Denies edema, graft tenderness. Denies edema. Denies graft tenderness."
    first = text.index("Denies edema, graft")
    second = text.index("Denies edema.")
    ms = [
        mention(text, text.index("edema"), text.index("edema") + 5, first, second),
        mention(text, second + 7, second + 12, second, second + 13),
    ]
    asked = sentences_to_ask(text, ms)
    assert [text[s.start : s.end].strip() for s in asked] == [
        "HPI: Denies edema, graft tenderness",
        "Denies graft tenderness",
    ]
    assert {s.reason for s in asked} == {"unmatched_item"}


def test_fallback_answer_is_checked_and_mapped_by_our_vocabulary() -> None:
    text = "HDR\n\nHPI: Denies edema, graft tenderness."
    start = text.index("Denies")
    sentence = Sentence(start, len(text), "history_of_present_illness", "unmatched_item")
    rules = [mention(text, text.index("edema"), text.index("edema") + 5, start, len(text))]
    answer = SentenceFindings(findings=[
        Finding(quote="graft tenderness", category="condition", negated=True,
                temporality="current", experiencer="patient"),
        Finding(quote="edema", category="condition", negated=True, temporality="current",
                experiencer="patient"),  # already found by the rules
        Finding(quote="kidney pain", category="condition", negated=True,
                temporality="current", experiencer="patient"),  # not in the sentence
        Finding(quote="CMV infection", category="lab", negated=False,
                temporality="current", experiencer="patient"),  # not in the sentence either
    ])  # fmt: skip
    run = FallbackRun()
    added = to_mentions(answer, text, sentence, rules, vocabulary(), "kidney", run)
    assert [(m.text, m.concept_id, m.method, m.negated) for m in added] == [
        ("graft tenderness", 0, "llm", True)  # no concept for it: 0, and review
    ]
    assert added[0].confidence < 0.7
    assert run.dropped_quotes == 2


def test_fallback_document_masks_dates_again() -> None:
    block = document("Biopsy on 01/11/2023 and MRN 1234567 noted", "history_of_present_illness")
    assert "01/11/2023" not in block and "1234567" not in block
    assert block.startswith("<section>history_of_present_illness</section>\n<sentence>")


@pytest.mark.parametrize("text", ["Stable graft function", "Continue current immunosuppression"])
def test_sentences_without_a_cue_are_never_sent(text: str) -> None:
    assert sentences_to_ask(f"H\n\n{text}.", []) == []
