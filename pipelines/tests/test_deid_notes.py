"""Note de-identification (pure Python)."""

from datetime import date, timedelta

import pytest

from txlens_datagen.cohort import FIRST_NAMES, SURNAME_ENDS, SURNAME_STARTS
from txlens_pipelines.deid_notes import (
    SURROGATE_FAMILY,
    SURROGATE_GIVEN,
    Identity,
    date_shift_days,
    deidentify,
    reidentify,
    surrogate_names,
    to_original_offset,
)

NOTE = """TRANSPLANT CLINIC NOTE
Aldermoor Transplant Center
Patient: Oakdale, Lorcan    MRN: A1001608    DOB: 06/30/1949
Date of service: 2023-07-20

HPI: 74 y/o M s/p heart Tx on 01/11/2023 (6 mo post-Tx). Biopsy 3/2/23. Mr. OAKDALE is well.
PMH:
- end-stage heart failure 2/2 dilated cardiomyopathy
FHx: Sister with HTN
Labs:
- Cr 1.19 (07/10)
A/P: Call 555-201-3344 or lorcan@example.org; lives at 12 Elm Grove Street. 93 yo uncle.
"""
WHO = Identity("A1001608", "Lorcan", "Oakdale", date(1949, 6, 30))
SHIFT = 10
NAMES = ("Maren", "Hollis")


def deid() -> str:
    return deidentify(NOTE, WHO, NAMES, SHIFT, date(2023, 7, 20)).text


def test_identifiers_are_gone() -> None:
    text = deid()
    for identifier in ("Oakdale", "OAKDALE", "Lorcan", "A1001608", "06/30/1949", "1949-06-30",
                       "555-201-3344", "lorcan@example.org", "12 Elm Grove Street"):  # fmt: skip
        assert identifier not in text


def test_surrogates_keep_the_note_readable() -> None:
    text = deid()
    assert "Patient: Hollis, Maren    MRN: [MRN]    DOB: 07/10/1949" in text
    assert "Date of service: 2023-07-30" in text
    assert "s/p heart Tx on 01/21/2023 (6 mo post-Tx)" in text  # same style, +10 days
    assert "Biopsy 3/12/23." in text
    assert "Mr. Hollis is well." in text
    assert "- Cr 1.19 (07/20)" in text  # year-less lab date
    assert "Call [PHONE] or [EMAIL]; lives at [ADDRESS]." in text
    assert "90+ yo uncle" in text and "74 y/o M" in text  # only ages >= 90 change


def test_clinical_shorthand_is_not_taken_for_a_date() -> None:
    assert "heart failure 2/2 dilated cardiomyopathy" in deid()


def test_round_trip_restores_the_original() -> None:
    result = deidentify(NOTE, WHO, NAMES, SHIFT, date(2023, 7, 20))
    assert reidentify(result.text, result.spans) == NOTE


def test_a_slice_can_be_restored_on_its_own() -> None:
    result = deidentify(NOTE, WHO, NAMES, SHIFT, date(2023, 7, 20))
    start = result.text.index("HPI:")
    end = result.text.index("\n", start)
    assert reidentify(result.text[start:end], result.spans, start).startswith(
        "HPI: 74 y/o M s/p heart Tx on 01/11/2023"
    )


def test_offsets_map_back_to_the_original_text() -> None:
    result = deidentify(NOTE, WHO, NAMES, SHIFT, date(2023, 7, 20))
    for word in ("dilated cardiomyopathy", "HTN", "Cr 1.19"):
        assert to_original_offset(result.spans, result.text.index(word)) == NOTE.index(word)
    inside = result.text.index("Hollis, Maren") + 2
    assert to_original_offset(result.spans, inside) == NOTE.index("Oakdale")


def test_impossible_date_is_masked() -> None:
    text = deidentify("Seen 13/45/2020.", WHO, NAMES, SHIFT, date(2023, 1, 1)).text
    assert text == "Seen [DATE]."


def test_year_less_date_after_the_note_belongs_to_the_previous_year() -> None:
    text = deidentify("Cr (12/30)", WHO, NAMES, 5, date(2024, 1, 3)).text
    assert text == "Cr (01/04)"  # 2023-12-30 + 5 days


@pytest.mark.parametrize("person_id", range(1, 200))
def test_shift_is_never_zero_and_stays_in_range(person_id: int) -> None:
    days = date_shift_days("k", "center_a", person_id)
    assert days != 0 and -182 <= days <= 182
    assert timedelta(days=days)


def test_surrogate_names_are_stable_and_never_the_real_name() -> None:
    first = surrogate_names("k", "center_a", 7, WHO)
    assert first == surrogate_names("k", "center_a", 7, WHO)
    for person_id in range(50):
        names = surrogate_names("k", "center_a", person_id, Identity("x", "Hollis", "Maren", None))
        assert not {n.lower() for n in names} & {"maren", "hollis"}  # either real name


def test_surrogate_lists_never_produce_a_generated_name() -> None:
    generated = {n for names in FIRST_NAMES.values() for n in names}
    generated |= {a + b for a in SURNAME_STARTS for b in SURNAME_ENDS}
    surrogates = set(SURROGATE_GIVEN) | set(SURROGATE_FAMILY)
    assert not {n.lower() for n in generated} & {n.lower() for n in surrogates}
