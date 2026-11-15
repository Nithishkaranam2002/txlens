"""Plain-English labels for the review queue's internal codes."""

from txlens_backend.review import labels
from txlens_backend.review.queue import decided_title
from txlens_omop.mappings import SOURCE_VOCABULARIES


def test_a_curated_key_reads_as_its_vocabulary_and_code() -> None:
    assert labels.curated_code("TXLENS_NOTE_ABBREV:abmr") == "Note abbreviation: “abmr”"
    assert (
        labels.curated_code("TXLENS_NOTE_ABBREV:kidney:tcmr") == "Note abbreviation: “kidney:tcmr”"
    )


def test_every_internal_vocabulary_has_a_label_and_standard_ones_keep_their_name() -> None:
    assert set(labels.VOCABULARIES) == set(SOURCE_VOCABULARIES)
    assert all(not label.startswith("TXLENS") for label in labels.VOCABULARIES.values())
    assert labels.vocabulary("SNOMED") == "SNOMED"


def test_gap_reasons_and_methods_read_as_plain_english() -> None:
    assert labels.gap_reason("low_score") == "Best match scored too low"
    assert labels.gap_reason("unmapped") == "No matching concept"
    assert labels.gap_reason("some_new_reason") == "some new reason"
    assert labels.method("vocabulary") == "Vocabulary match"


def test_decided_titles_show_no_internal_keys() -> None:
    assert decided_title("curated_mapping", "TXLENS_DRUG:tacro") == "Drug name: “tacro”"
    assert (
        decided_title("biopsy_document", "6f1c0a51-0000-4000-8000-000000000000") == "Biopsy report"
    )
    assert decided_title("note_mention", "N-000001-003:296-312") == "Note mention"
    assert decided_title("condition_mapping", "kidney graft rejection") == (
        "Condition text “kidney graft rejection”"
    )
