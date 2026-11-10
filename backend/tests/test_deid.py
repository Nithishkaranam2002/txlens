"""Pseudonyms, date shifts, age bands and small-cell suppression."""

from datetime import date

import pytest

from txlens_backend.deid import age_band, date_shift_days, pseudonym, shift, suppress

KEY = "k" * 40


def test_pseudonym_is_stable_keyed_and_tenant_specific() -> None:
    first = pseudonym(KEY, "center_a", 100_000_001)
    assert first == pseudonym(KEY, "center_a", 100_000_001)
    assert first.startswith("R-") and len(first) == 18
    assert first != pseudonym(KEY, "center_b", 100_000_001)
    assert first != pseudonym("other" * 8, "center_a", 100_000_001)  # rotating the key unlinks
    assert "100000001" not in first


def test_pseudonyms_do_not_collide_within_a_center() -> None:
    ids = {pseudonym(KEY, "center_a", 100_000_000 + n) for n in range(5000)}
    assert len(ids) == 5000


def test_date_shift_is_per_patient_nonzero_and_bounded() -> None:
    shifts = [date_shift_days(KEY, "center_a", 100_000_000 + n) for n in range(2000)]
    assert all(1 <= abs(s) <= 182 for s in shifts)
    assert len(set(shifts)) > 300  # spread out, not one global offset
    assert date_shift_days(KEY, "center_a", 100_000_001) == shifts[1]


def test_shift_preserves_intervals_within_a_patient() -> None:
    days = date_shift_days(KEY, "center_a", 7)
    transplant, biopsy = date(2024, 1, 5), date(2024, 3, 1)
    shifted_transplant, shifted_biopsy = shift(transplant, days), shift(biopsy, days)
    assert shifted_transplant is not None and shifted_biopsy is not None
    assert shifted_biopsy - shifted_transplant == biopsy - transplant
    assert shifted_transplant != transplant
    assert shift(None, days) is None


@pytest.mark.parametrize(
    ("year", "band"), [(1980, "40-44"), (1976, "45-49"), (1975, "45-49"), (1934, "90+"),
                       (2024, "0-4")]
)  # fmt: skip
def test_age_bands_at_a_reference_date(year: int, band: str) -> None:
    assert age_band(year, date(2024, 6, 1)) == band


@pytest.mark.parametrize(("count", "shown"), [(0, 0), (1, "<11"), (10, "<11"), (11, 11)])
def test_small_counts_are_suppressed(count: int, shown: int | str) -> None:
    assert suppress(count, 11) == shown
