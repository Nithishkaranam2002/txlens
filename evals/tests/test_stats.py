"""Bootstrap intervals and slot-style precision / recall / F1 (no database)."""

from txlens_evals.stats import Estimate, bootstrap, f1, precision, ratio, recall


def test_slot_metrics() -> None:
    assert (precision([8, 2, 4]), recall([8, 2, 4])) == (0.8, 8 / 12)
    assert f1([0, 0, 0]) == 0.0


def test_interval_contains_the_estimate_narrows_with_more_units_and_is_reproducible() -> None:
    small = [(1, 1)] * 7 + [(0, 1)] * 3
    large = small * 50
    a, b = bootstrap(small, ratio(0, 1)), bootstrap(large, ratio(0, 1))
    assert a.low <= a.value == 0.7 <= a.high and b.low <= b.value <= b.high
    assert b.high - b.low < a.high - a.low
    assert bootstrap(small, ratio(0, 1)) == a  # fixed seed


def test_no_units_and_text() -> None:
    assert bootstrap([], ratio(0, 1)) == Estimate(0.0, 0.0, 0.0)
    assert Estimate(0.942, 0.938, 0.946).text() == "94.2% (93.8%-94.6%)"
