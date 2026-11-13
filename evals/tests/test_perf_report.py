"""The latency report: percentiles, slowest first, and the before/after comparison."""

from txlens_evals.perf import Case, Timing, percentile, report


def timing(name: str, p50: float, p95: float) -> Timing:
    return Timing(Case("clinician", name, "/x"), p50, p95, p95 + 1, 200, 20)


def test_nearest_rank_percentile() -> None:
    values = [float(v) for v in range(1, 21)]
    assert (percentile(values, 0.5), percentile(values, 0.95), percentile(values, 1.0)) == (
        10.0, 19.0, 20.0,
    )  # fmt: skip


def test_report_lists_slowest_first_with_the_baseline_beside_it() -> None:
    timings = [timing("fast", 2, 3), timing("slow", 20, 22)]
    baseline = {"slow": {"p50": 29.0, "p95": 49.0, "max": 302.0}}
    pages = {"pages": {"Worklist": {"p50": 137.0, "max": 260.0, "runs": 20}}}
    before_pages = {"pages": {"Worklist": {"p50": 161.0}}}
    text = report(timings, "after", pages, baseline, before_pages)
    rows = [line for line in text.splitlines() if line.startswith("| slow") or
            line.startswith("| fast")]  # fmt: skip
    assert rows[0].startswith("| slow | clinician | 29 / 49 / 302 | 20 | 22 |")
    assert rows[1].startswith("| fast | clinician | — | 2 | 3 |")  # no baseline row
    assert "| Worklist | 161 | 137 | 260 | 20 |" in text


def test_report_without_baseline_has_no_before_column() -> None:
    text = report([timing("only", 5, 6)], "now", None, None)
    assert "before" not in text and "| only | clinician | 5 | 6 | 7 | 20 | 200 |" in text
