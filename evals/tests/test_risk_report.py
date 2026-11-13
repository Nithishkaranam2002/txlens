"""Risk model report helpers (no database)."""

from datetime import date

import numpy as np
import pandas as pd

from txlens_evals.risk_model import Truth, episodes_caught, render, visit_reason


def truth() -> Truth:
    return Truth(
        rejections={"p1": [date(2024, 6, 1)]}, borderline={},
        follow_up={"p1": date(2026, 9, 30), "p2": date(2024, 5, 1)},
        ends={"p1": None, "p2": None},
        draw_reasons={("p1", date(2024, 5, 1)): {"for_cause"},
                      ("p1", date(2024, 4, 1)): {"surveillance", "for_cause"}},
        surveillance_draws={"p1": [date(2024, 4, 1)]},
    )  # fmt: skip


def test_truth_label_uses_the_same_window_and_censoring() -> None:
    t = truth()
    assert t.label("p1", date(2024, 3, 3), date(2026, 9, 30)) == 1.0  # biopsy on day 90
    assert t.label("p1", date(2024, 3, 2), date(2026, 9, 30)) == 0.0
    assert np.isnan(t.label("p2", date(2024, 4, 1), date(2026, 9, 30)))  # follow-up too short


def test_visit_reason_prefers_scheduled() -> None:
    t = truth()
    assert visit_reason(t, "p1", date(2024, 4, 1)) == "surveillance"
    assert visit_reason(t, "p1", date(2024, 5, 1)) == "for_cause"
    assert visit_reason(t, "p1", date(2024, 1, 1)) == "unknown"


def test_an_episode_is_caught_by_any_alert_before_its_biopsy() -> None:
    part = pd.DataFrame({
        "key": ["a", "a", "b", "c"],
        "next_rejection": [date(2024, 6, 1), date(2024, 6, 1), date(2024, 7, 1), None],
    })  # fmt: skip
    assert episodes_caught(part, np.array([False, True, False, True])) == (1, 2)


def test_report_carries_the_label() -> None:
    text = render([("Data", ["x"])], date(2026, 9, 30))
    assert "not clinically validated, decision support only" in text
    assert "## 1. Data" in text
