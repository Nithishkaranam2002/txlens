"""Rules-only biopsy extractor and slot scoring of the three systems (no database)."""

from datetime import date
from typing import Any

from txlens_backend.documents.ocr import Line, OcrResult, PageSize, Word
from txlens_evals.biopsy_extraction import Truth
from txlens_evals.biopsy_rules import digit, extract
from txlens_evals.biopsy_systems import Report, counts, evaluate, fully_correct, llm_values

KIDNEY = (
    "TEST ONLY MEDICAL CENTER",
    "Procedure date: 05/06/2024 Reported: 05/08/2024",
    "MICROSCOPIC DESCRIPTION",
    "Specimen adequacy: adequate",
    "Banff lesion scores: i2 t2 v0 g0 ptcO ci1 ct1 cv0",
    "C4d (IHC, peritubular capillaries): C4d0",
    "DIAGNOSIS",
    "- Acute T-cell mediated rejection, Banff grade IA.",
    "- Polyomavirus (BK) nephropathy, SV40 immunostain positive.",
    "Electronically signed: Testa Reviewer, MD",
)
HEART = (
    "Procedure date: 03/01/2024",
    "ACR grade (ISHLT 2004): 2R",
    "pAMR grade (ISHLT 2013): pAMR 1 (I+)",
)


def ocr(lines: tuple[str, ...]) -> OcrResult:
    built = tuple(Line(n, 1, tuple(Word(w, (0, 0, 1, 1), 0.9) for w in t.split()))
                  for n, t in enumerate(lines, start=1))  # fmt: skip
    return OcrResult("tesseract", (PageSize(612, 792),), built)


def test_rules_read_a_kidney_report_including_ocr_lookalikes() -> None:
    values = extract(ocr(KIDNEY), "kidney")
    assert values["biopsy_date"] == date(2024, 5, 6)
    assert (values["banff_i"], values["banff_ptc"], values["c4d"]) == (2, 0, 0)  # "ptcO"
    assert (values["diagnosis"], values["tcmr_grade"]) == ("TCMR", "IA")
    assert (values["adequacy"], values["additional_finding"]) == ("adequate", "BK_nephropathy")


def test_rules_read_heart_grades_with_their_suffix_and_derive_the_diagnosis() -> None:
    values = extract(ocr(HEART), "heart")
    assert (values["acr_grade"], values["pamr_grade"], values["diagnosis"]) == (
        "2R", "pAMR 1 (I+)", "mixed")  # fmt: skip
    assert digit("x") is None


TRUE_HEART = {"biopsy_date": date(2024, 3, 1), "diagnosis": "mixed", "acr_grade": "2R",
              "pamr_grade": "pAMR 2"}  # fmt: skip


def report(**systems: dict[str, Any]) -> Report:
    truth = Truth("B1", "heart", "p.pdf", TRUE_HEART, (), "")
    r = Report(truth, "heart", in_review=True)
    r.values = {"rules only": {}, "LLM only": {}, "hybrid": {}} | systems
    r.llm_cost = dict.fromkeys(r.values, 0.0)
    r.llm_ms = {k: [] for k in r.values}
    return r


def test_slot_scoring_counts_a_wrong_value_as_fp_and_fn() -> None:
    r = report(hybrid={"biopsy_date": "2024-03-01", "diagnosis": "mixed", "acr_grade": "2R",
                       "pamr_grade": "pAMR 1 (I+)"})  # fmt: skip
    assert counts(r, "hybrid") == (3, 1, 1)
    assert not fully_correct(r, "hybrid")
    assert counts(r, "LLM only") == (0, 0, 4)  # nothing found: four misses


def test_only_the_hybrid_routes_wrong_reports_to_review() -> None:
    wrong: dict[str, Any] = {"biopsy_date": "2024-03-01", "diagnosis": "TCMR",
                             "acr_grade": "2R", "pamr_grade": "pAMR 2"}  # fmt: skip
    r = report(**{"rules only": wrong, "LLM only": wrong, "hybrid": wrong})
    assert evaluate([r], "hybrid").wrong_loaded == 0  # it went to review
    assert evaluate([r], "LLM only").wrong_loaded == 1


def test_llm_heart_diagnosis_follows_from_the_grades() -> None:
    values = llm_values({"acr_grade": {"value": "0R"}, "pamr_grade": {"value": "pAMR 0"}}, "heart")
    assert values["diagnosis"] == "no_rejection"
