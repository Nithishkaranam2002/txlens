"""Note NLP: vocabulary rules, medspaCy attributes, LLM-fallback triggers and checks (no DB)."""

from dataclasses import replace
from datetime import date
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
from txlens_backend.notes.nlp import (
    REVIEW_PAST_REJECTION,
    Mention,
    NoteNlp,
    past_episode_cue,
    service_date,
)
from txlens_backend.notes.results import needs_review
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
ABMR, BIOPSY = 37472871, 4041358
CURATED = [
    Term("htn", HTN, "condition", True),
    Term("t2dm", T2DM, "condition", True),
    Term("tcmr", RENAL_TCMR, "condition", True, "kidney"),
    Term("tcmr", CARDIAC_ACR, "condition", True, "heart"),
    Term("cr", 3016723, "lab", True),
    Term("dd-cfdna", 3966008, "lab", True),
    Term("ktx", 4322471, "procedure", True),
    Term("abmr", ABMR, "condition", True),
    Term("biopsy", BIOPSY, "procedure", True),
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
    rejection = frozenset({RENAL_TCMR, CARDIAC_ACR, ABMR})
    return NoteNlp(replace(vocabulary(), rejection=rejection), context=context)


def hpi(sentence: str, service: str = "2025-01-04") -> dict[str, Mention]:
    """Mentions of one HPI sentence in a kidney note dated `service`."""
    note = f"CLINIC NOTE\nDate of service: {service}\n\nHPI: {sentence}\n"
    return {m.text: m for m in pipeline().extract(note, "kidney")}


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


def test_treated_rejection_with_a_past_date_is_historical() -> None:
    m = hpi("Bx 2024-11-02 showed ABMR, treated.")["ABMR"]
    assert m.temporality == "historical" and m.review_reason is None


def test_rejection_being_treated_stays_current() -> None:
    assert hpi("ABMR being treated with IVIG.")["ABMR"].temporality == "current"


def test_biopsy_result_line_stays_current() -> None:
    m = hpi("Result: TCMR, Banff grade 1A.")["TCMR"]
    assert m.temporality == "current" and m.review_reason is None


def test_will_arrange_a_biopsy_is_hypothetical() -> None:
    assert hpi("Will arrange for-cause biopsy.")["biopsy"].temporality == "hypothetical"


def test_current_rejection_with_an_earlier_date_goes_to_review() -> None:
    m = hpi("Bx 2024-12-20 showed TCMR.")["TCMR"]
    assert m.temporality == "current" and m.review_reason == REVIEW_PAST_REJECTION
    assert needs_review(m, 0.7)


def test_same_day_dated_rejection_is_current_without_review() -> None:
    m = hpi("Bx 2025-01-04 showed TCMR.")["TCMR"]
    assert m.temporality == "current" and m.review_reason is None


def test_review_rule_only_concerns_rejection() -> None:
    assert hpi("Bx 2024-12-20 showed edema.")["edema"].review_reason is None


def test_dates_and_cues() -> None:
    assert service_date("X\nDate of service: 3/22/24\n") == date(2024, 3, 22)
    assert service_date("no header") is None
    assert past_episode_cue("Bx 11/04/2024 showed ABMR", date(2025, 1, 1))
    assert not past_episode_cue("Bx 11/04/2024 showed ABMR", None)
    assert past_episode_cue("s/p ABMR", None)
    assert not past_episode_cue("Creatinine (02/19) stable", date(2025, 1, 1))


def test_hybrid_system_name_changes_with_the_rules_version() -> None:
    from txlens_backend.notes.service import system_name

    assert system_name(pipeline(), True).startswith("txlens-medspacy-2+llm-")
    assert system_name(pipeline(), False) == "txlens-medspacy-2"
