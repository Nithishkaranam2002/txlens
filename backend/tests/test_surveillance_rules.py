"""Each rule kind on hand-built series: when it fires, when it does not, and what it cites."""

from surveillance_testutils import config, day, series

from txlens_backend.surveillance.config import Severity
from txlens_backend.surveillance.engine import evaluate


def fired(rule_id: str, **markers: list[tuple[int, float]]) -> list[str]:
    """Reasons of the flags `rule_id` raises on day 200 (other rules ignored)."""
    flags = evaluate(config(), series(**markers), day(200))
    return [f.reason for f in flags if f.rule_id == rule_id]


# --- schedule adherence (due days 30, 61, 91, then 183, 274, ...; tolerance 14) ---------


def test_overdue_when_window_closed_without_result() -> None:
    on_time = [(30, 0.2), (61, 0.2), (91, 0.2)]
    flags = evaluate(config(), series(m=on_time), day(183 + 15))
    assert [f.rule_id for f in flags] == ["overdue"]
    assert flags[0].severity is Severity.LOW
    assert "month 6" in flags[0].reason and "±14 days" in flags[0].reason
    assert flags[0].evidence[0].role == "due" and flags[0].evidence[0].value is None


def test_not_overdue_inside_tolerance_or_when_done() -> None:
    on_time = [(30, 0.2), (61, 0.2), (91, 0.2)]
    assert evaluate(config(), series(m=on_time), day(183 + 14)) == []  # window still open
    assert evaluate(config(), series(m=[*on_time, (183 - 14, 0.2)]), day(198)) == []
    assert evaluate(config(), series(m=[*on_time, (190, 0.2)]), day(198)) == []  # late but done


def test_a_late_result_clears_an_overdue_flag() -> None:
    late = [(30, 0.2), (61, 0.2), (91, 0.2), (210, 0.2)]
    assert evaluate(config(), series(m=late), day(205))  # overdue before the late test
    assert evaluate(config(), series(m=late), day(210)) == []


# --- threshold ----------------------------------------------------------------------------


def test_threshold_highest_level_wins() -> None:
    flags = evaluate(config(), series(m=[(30, 0.2), (61, 0.2), (91, 0.2), (190, 1.3)]), day(200))
    [flag] = [f for f in flags if f.rule_id == "over"]
    assert flag.severity is Severity.HIGH
    assert flag.reason.startswith("Marker 1.3% on 2024-07-09 is at or above the 1%")
    assert "(high risk)" in flag.reason
    assert [(e.value, e.role) for e in flag.evidence] == [(1.3, "latest")]


def test_threshold_watch_level_and_below() -> None:
    assert "(watch)" in fired("over", m=[(190, 0.5)])[0]
    assert fired("over", m=[(190, 0.49)]) == []


def test_threshold_looks_only_at_the_latest_result_up_to_as_of() -> None:
    assert fired("over", m=[(150, 1.5), (190, 0.2)]) == []  # high, then back to normal
    assert fired("over", m=[(190, 0.2), (250, 1.5)]) == []  # future result is invisible


def test_threshold_result_too_old_stops_firing() -> None:
    assert fired("over", m=[(109, 1.5)]) == []  # 91 days old; 90 is the max age
    assert fired("over", m=[(110, 1.5)])


# --- rising trend -------------------------------------------------------------------------


def test_two_consecutive_increases() -> None:
    [reason] = fired("rising", m=[(120, 0.2), (150, 0.3), (190, 0.4)])
    assert reason.startswith("Marker rose 2 times in a row (0.2% → 0.3% → 0.4%")


def test_relative_rise() -> None:
    [reason] = fired("rising", m=[(150, 0.4), (190, 0.2), (195, 0.45)])
    assert "rose 125% from 0.2%" in reason


def test_no_trend_in_the_noise_floor_or_when_flat() -> None:
    assert fired("rising", m=[(120, 0.1), (150, 0.15), (190, 0.2)]) == []  # below min_latest
    assert fired("rising", m=[(120, 0.4), (150, 0.5), (190, 0.5)]) == []
    assert fired("rising", m=[(190, 0.9)]) == []


# --- relative change vs own baseline --------------------------------------------------------


def test_creatinine_rise_from_own_baseline() -> None:
    [reason] = fired("cr_up", c=[(100, 1.0), (130, 1.2), (160, 1.1), (190, 1.5)])
    assert "rose 36% to 1.5 mg/dL" in reason
    assert "baseline 1.1 mg/dL (median of 3 results)" in reason


def test_creatinine_baseline_window_and_minimum_points() -> None:
    assert fired("cr_up", c=[(100, 1.0), (130, 1.2), (160, 1.1), (190, 1.3)]) == []  # +18%
    assert fired("cr_up", c=[(90, 1.0), (190, 1.6)]) == []  # baseline result too old
    assert fired("cr_up", c=[(15, 3.0), (20, 1.0), (25, 1.5)]) == []  # before start_after_days


def test_relative_change_evidence_lists_baseline_and_latest() -> None:
    flags = evaluate(config(), series(c=[(130, 1.0), (160, 1.0), (190, 1.5)]), day(200))
    [flag] = [f for f in flags if f.rule_id == "cr_up"]
    assert [e.role for e in flag.evidence] == ["baseline", "baseline", "latest"]


# --- range by phase -------------------------------------------------------------------------


def test_range_depends_on_time_since_anchor() -> None:
    assert fired("level", t=[(190, 10.0)]) == ["Drug level 10 ng/mL on 2024-07-09 is above "
                                                "the target 5 ng/mL to 8 ng/mL for month 6 after "
                                                "transplant date"]  # fmt: skip
    assert fired("level", t=[(190, 6.0)]) == []
    early = evaluate(config(), series(t=[(40, 6.0)]), day(45))
    assert any("below the target 8 ng/mL to 12 ng/mL" in f.reason for f in early)


# --- new positive -------------------------------------------------------------------------


def test_first_positive_after_negative() -> None:
    [reason] = fired("dsa", d=[(30, 200.0), (150, 4000.0)])
    assert reason == ("New positive DSA 4000 MFI on 2024-05-30 after a negative 200 MFI on "
                      "2024-01-31 (positive at or above 1000 MFI)")  # fmt: skip


def test_positive_stays_on_worklist_for_active_days_only() -> None:
    assert fired("dsa", d=[(30, 200.0), (110, 4000.0)])  # 90 days ago: still shown
    assert fired("dsa", d=[(30, 200.0), (109, 4000.0)]) == []
    assert fired("dsa", d=[(30, 4000.0), (150, 200.0), (180, 5000.0)]) == []  # not new


def test_end_of_surveillance_and_before_anchor() -> None:
    sick = {"m": [(190, 3.0)]}
    assert evaluate(config(), series(day(195), **sick), day(200)) == []
    assert evaluate(config(), series(**sick), day(-1)) == []
