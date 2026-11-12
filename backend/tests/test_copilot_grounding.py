"""Quote grounding: claims are shown only with quotes found verbatim in the cited abstract."""

import pytest

from txlens_backend.copilot.grounding import Claim, Quote, check, normalize, quote_found, render
from txlens_backend.copilot.retrieval import Document

DOC = Document(pmid=1, title="A made-up study", journal="J", year=2024,
               abstract="Background: dd-cfDNA was measured. Results: A fraction of 1% or more "
               "identified antibody‑mediated rejection with 85% sensitivity — and 70% "  # noqa: RUF001 (typography under test)
               "specificity.")  # fmt: skip
DOCS = {1: DOC}


@pytest.mark.parametrize(
    ("quote", "found"),
    [
        ("A fraction of 1% or more identified", True),
        ("a FRACTION   of 1% or more\nidentified", True),  # case and whitespace
        ("rejection with 85% sensitivity - and 70% specificity", True),  # em dash -> hyphen
        ('"A fraction of 1% or more identified."', True),  # surrounding quotes and period
        ("A fraction of 2% or more identified", False),  # changed number
        ("A fraction of 1% identified rejection", False),  # paraphrase
        ("85% sensitivity", False),  # too short
        ("A made-up study dd-cfDNA was measured", False),  # joins title and text
    ],
)
def test_quotes_must_be_verbatim_and_long_enough(quote: str, found: bool) -> None:
    assert quote_found(Quote(pmid=1, quote=quote), DOCS) is found


def test_a_quote_must_come_from_the_abstract_it_names() -> None:
    assert not quote_found(Quote(pmid=2, quote="A fraction of 1% or more identified"), DOCS)


def test_numbers_in_a_claim_must_appear_in_its_quotes() -> None:
    quote = Quote(pmid=1, quote="identified antibody-mediated rejection with 85% sensitivity")
    assert check(Claim(text="It had 85% sensitivity.", quotes=[quote]), DOCS,
                 data_allowed=False).text  # fmt: skip
    dropped = check(Claim(text="It had 85% sensitivity and 70% specificity.", quotes=[quote]),
                    DOCS, data_allowed=False)  # fmt: skip
    assert dropped.text is None and "70" in str(dropped.problem)


def test_claims_without_quotes_need_data() -> None:
    claim = Claim(text="Our center has 675 kidney recipients.")
    assert check(claim, DOCS, data_allowed=True).text == claim.text
    assert check(claim, DOCS, data_allowed=False).problem == "no quote"


def test_only_verified_quotes_are_kept_and_cited() -> None:
    good = Quote(pmid=1, quote="A fraction of 1% or more identified")
    bad = Quote(pmid=1, quote="made up words that are not in it")
    checked = check(Claim(text="A 1% fraction identified ABMR.", quotes=[good, bad]), DOCS,
                    data_allowed=False)  # fmt: skip
    assert checked.quotes == [good]
    assert render(checked.text or "", checked.quotes) == "A 1% fraction identified ABMR [PMID:1]."


def test_normalize_is_typography_insensitive() -> None:
    assert normalize("“Antibody–mediated” Rejection.") == 'antibody-mediated" rejection'  # noqa: RUF001 (typography under test)


def test_comparison_signs_match_their_ascii_form() -> None:
    doc = Document(pmid=3, title="T", journal="J", abstract="dd-cfDNA \u22651% was associated "
                   "with active antibody-mediated rejection in most cohorts.")  # fmt: skip
    assert quote_found(Quote(pmid=3, quote="dd-cfDNA >=1% was associated with active"), {3: doc})
