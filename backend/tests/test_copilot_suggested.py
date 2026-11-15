"""Matching typed questions to the suggested ones, and spotting "help" questions."""

import pytest

from txlens_backend.copilot.suggested import SUGGESTED, asks_for_help, match, normalize

MEDIAN, KIDNEY_COUNT = SUGGESTED[0], SUGGESTED[1]


def test_normalize_ignores_case_spaces_punctuation_and_hyphens() -> None:
    assert normalize("  Median DD-cfDNA,\tby   organ?? ") == "median ddcfdna by organ"


@pytest.mark.parametrize("typed", [
    MEDIAN,
    "median dd-cfdna in the first year after transplant, by organ?",
    "  MEDIAN  dd-cfDNA in the first year after transplant by organ  ",
    "Median ddcfDNA in the first year after transplant - by organ.",
    "Median dd-cfDNA in the frist year after transplnt, by organ",  # two typos
    "Median dd-cfDNA in first year after transplant, by organ",  # "the" left out
])  # fmt: skip
def test_near_identical_typing_finds_the_saved_question(typed: str) -> None:
    assert match(typed) == MEDIAN


@pytest.mark.parametrize("typed", [
    "Median dd-cfDNA in the second year after transplant, by organ",
    "Median dd-cfDNA in the first year after transplant, by donor type",
    "How many kidney recipients had a dd-cfDNA result of 2% or more in the first year after "
    "transplant?",
    "How many heart recipients had a dd-cfDNA result of 1% or more in the first year after "
    "transplant?",
    "List the MRNs of the patients without antibody-mediated rejection.",
    "Mdian dd-cfDNA in the frist yaer after transplnt, by organ",  # four typos
    "How many heart recipients are there?",
])  # fmt: skip
def test_a_different_question_is_not_matched(typed: str) -> None:
    assert match(typed) is None


def test_every_suggested_question_matches_itself_in_lower_case() -> None:
    assert [match(s.lower().rstrip("?.")) for s in SUGGESTED] == list(SUGGESTED)
    assert match(KIDNEY_COUNT.upper()) == KIDNEY_COUNT


@pytest.mark.parametrize("typed", ["help", "Help!", "What is this about?", "what can you do",
                                   "How does this work?", "hi", "what is this app"])  # fmt: skip
def test_help_questions(typed: str) -> None:
    assert asks_for_help(typed)


@pytest.mark.parametrize("typed", ["help me find kidney recipients with ABMR",
                                   "What is the median eGFR?", MEDIAN])  # fmt: skip
def test_real_questions_are_not_help(typed: str) -> None:
    assert not asks_for_help(typed)
