"""Text -> concept matcher and the clinical-qualifier guard (pure Python, no Spark)."""

import pytest

from txlens_pipelines.textmatch.matcher import ConceptMatcher, Term, token_key
from txlens_pipelines.textmatch.qualifiers import qualifiers, same_qualifiers

T2DM, T1DM, HTN, CKD3, CKD4 = 201826, 201254, 316866, 443597, 443612
ACUTE_REJ, CHRONIC_REJ, DKD = 4128371, 4128372, 192279
PREFERRED = {
    T2DM: "Type 2 diabetes mellitus",
    T1DM: "Type 1 diabetes mellitus",
    HTN: "Hypertensive disorder",
    CKD3: "Chronic kidney disease stage 3",
    CKD4: "Chronic kidney disease stage 4",
    ACUTE_REJ: "Acute rejection of renal transplant",
    CHRONIC_REJ: "Chronic rejection of renal transplant",
    DKD: "Disorder of kidney due to diabetes mellitus",
}
SYNONYMS = [
    Term(T2DM, "Diabetes mellitus type 2"),
    Term(T2DM, "T2DM - diabetes mellitus type 2"),
    Term(HTN, "Hypertension"),
    Term(DKD, "Diabetic nephropathy"),
]


def matcher(only: set[int] | None = None, curated: dict[str, int] | None = None) -> ConceptMatcher:
    keep = only or set(PREFERRED)
    terms = [Term(c, n) for c, n in PREFERRED.items() if c in keep]
    terms += [t for t in SYNONYMS if t.concept_id in keep]
    return ConceptMatcher(terms, PREFERRED, curated or {})


def test_qualifiers_are_extracted_and_roman_numerals_read() -> None:
    assert qualifiers("Diabetes mellitus type II") == {("type", "2")}
    assert qualifiers("T1DM") == {("type", "1")}
    assert qualifiers("CKD stage 3a") == {("stage", "3a"), ("acuity", "chronic")}
    assert qualifiers("Chronic rejection, left kidney") == {("acuity", "chronic"), ("side", "left")}
    assert qualifiers("Hypertension") == frozenset()
    assert qualifiers("CKD stage 3") == qualifiers("Chronic kidney disease stage 3")


def test_missing_or_extra_qualifiers_differ() -> None:
    assert same_qualifiers("Type 2 diabetes", "Diabetes mellitus type 2")
    assert not same_qualifiers("diabetes", "Type 2 diabetes mellitus")
    assert not same_qualifiers("rejection", "Acute rejection of renal transplant")


def test_exact_synonym_case_insensitive() -> None:
    result = matcher().match("  HYPERTENSION ")
    assert (result.concept_id, result.method, result.confidence) == (HTN, "exact", 1.0)


def test_normalized_word_order_and_filler() -> None:
    result = matcher().match("Type 2 diabetes mellitus (disorder)")
    assert (result.concept_id, result.method) == (T2DM, "normalized")


def test_fuzzy_accepts_close_name_with_same_qualifiers() -> None:
    result = matcher().match("Diabetic nephropathies")
    assert (result.concept_id, result.method) == (DKD, "fuzzy")
    assert result.confidence >= 0.90


def test_curated_map_wins_over_automatic_tiers() -> None:
    result = matcher(curated={"hypertension": T2DM}).match("Hypertension")
    assert (result.concept_id, result.method) == (T2DM, "curated")


def test_low_score_goes_to_review_with_suggestion() -> None:
    result = matcher().match("Type 2 diabetes")
    assert result.concept_id is None and result.method == "review"
    assert result.suggestion == T2DM and result.reason == "low_score"


@pytest.mark.parametrize(
    ("text", "only", "wrong"),
    [
        ("Type 1 diabetes", {T2DM, HTN}, T2DM),
        ("Type 1 diabetes mellitus", {T2DM, HTN}, T2DM),
        ("chronic rejection of renal transplant", {ACUTE_REJ, HTN}, ACUTE_REJ),
        ("Chronic kidney disease stage 3", {CKD4, HTN}, CKD4),
        ("CKD stage 3", {CKD4, HTN}, CKD4),
    ],
)
def test_qualifier_mismatch_is_never_auto_accepted(text: str, only: set[int], wrong: int) -> None:
    result = matcher(only).match(text)
    assert result.concept_id != wrong
    assert result.method == "review"


def test_abbreviation_synonym_with_implied_acuity_is_accepted() -> None:
    m = ConceptMatcher([Term(CKD3, "CKD stage 3")], PREFERRED, {})
    assert m.match("CKD stage 3").concept_id == CKD3


def test_qualifier_variant_finds_its_own_concept_when_present() -> None:
    assert matcher().match("Type 1 diabetes mellitus").concept_id == T1DM
    assert matcher().match("Chronic kidney disease stage 3").concept_id == CKD3
    assert matcher().match("chronic rejection of renal transplant").concept_id == CHRONIC_REJ


def test_fuzzy_near_miss_on_stage_goes_to_review() -> None:
    # Only one character apart, so the score is high; the guard still refuses it.
    result = matcher({CKD4}).match("Chronic kidney disease stage 3")
    assert result.method == "review" and result.reason == "qualifier_mismatch"
    assert result.suggestion == CKD4


def test_two_concepts_with_the_same_name_are_ambiguous() -> None:
    terms = [Term(1, "Graft failure"), Term(2, "Graft failure")]
    result = ConceptMatcher(terms, {}, {}).match("graft failure")
    assert result.method == "review" and result.reason == "ambiguous"


def test_token_key() -> None:
    assert token_key("Diabetes mellitus type II (disorder)") == "2 diabetes mellitus type"
