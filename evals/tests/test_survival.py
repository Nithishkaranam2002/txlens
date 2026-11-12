"""Survival report helpers (no database, no R)."""

import pytest

from txlens_backend.survival.landmark import HazardRatio
from txlens_backend.survival.simulate import simulate
from txlens_evals.survival import (
    Accuracy,
    clopper_pearson,
    count,
    incidence_rows,
    main_verdict,
    render,
)


def ratio(design: str, hr: float) -> HazardRatio:
    return HazardRatio(design, hr, hr * 0.8, hr * 1.2, 0.0, 0.1, 100, 20)


def test_exact_interval_stays_informative_at_100_percent() -> None:
    low, high = clopper_pearson(304, 304)
    assert high == 1.0 and low == pytest.approx(0.988, abs=0.001)  # the "rule of three" region
    assert clopper_pearson(0, 10)[0] == 0.0


def test_accuracy_counts() -> None:
    acc = Accuracy(tp=8, fp=2, fn=2, tn=88)
    assert (acc.sensitivity, acc.specificity, acc.ppv) == (0.8, 88 / 90, 0.8)


def test_small_counts_are_suppressed_in_the_report() -> None:
    assert (count(7), count(0), count(95)) == ("<11", "0", "95")


def test_the_main_data_verdict_follows_the_numbers() -> None:
    biased = [ratio("naive", 7.6), ratio("landmark", 8.6), ratio("tv", 9.6)]
    assert main_verdict(biased).startswith("The naive design gives the smallest")
    assert "does not give the smallest" in main_verdict([ratio("n", 9.0), *biased[1:]])


def test_cumulative_incidence_rows_show_1_minus_km_overstating() -> None:
    rows, gap = incidence_rows("simulated", simulate(n=2000))
    assert len(rows) == 3 and gap > 0
    assert rows[0].startswith("| simulated | 1 |")


def test_report_numbers_its_sections() -> None:
    text = render([("First", ["a"]), ("Second", ["b"])])
    assert "## 1. First" in text and "## 2. Second" in text and "Synthetic data only" in text
