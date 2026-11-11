"""Surveillance replay scoring: alert stretches, detection and lead time, onset classes,
daily counts, overdue scoring, and a full report on a small generated dataset."""

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from txlens_backend.surveillance.config import Severity
from txlens_backend.surveillance.model import Evidence, Flag
from txlens_datagen.config import GeneratorConfig
from txlens_datagen.generate import generate
from txlens_datagen.oncology import OncologyConfig, generate_oncology
from txlens_evals.surveillance_replay import (
    CAVEAT,
    Episode,
    PatientReplay,
    classify_onsets,
    daily_counts,
    detect,
    intervals,
    run,
    score_overdue,
)

D0 = date(2024, 1, 1)


def d(n: int) -> date:
    return D0 + timedelta(days=n)


def flag(rule: str, severity: Severity = Severity.HIGH) -> Flag:
    return Flag("c", 1, rule, rule, severity, "why", (Evidence("m", D0, 1.0, "latest"),))


def test_intervals_join_days_with_open_flags_and_close_at_the_end() -> None:
    states = [(d(0), []), (d(5), [flag("a")]), (d(9), [flag("a"), flag("b")]), (d(12), []),
              (d(20), [flag("b", Severity.LOW)]), (d(30), [flag("a")])]  # fmt: skip
    alerts = intervals(states, d(40), lambda f: f.severity is Severity.HIGH)
    assert alerts == [(d(5), d(12), frozenset({"a"})), (d(30), d(40), frozenset({"a"}))]
    assert [(s, e) for s, e, _ in intervals(states, d(40), lambda f: True)] == [
        (d(5), d(12)), (d(20), d(40))
    ]  # fmt: skip


def patient(alerts: list[tuple[int, int]], end: int = 400) -> PatientReplay:
    return PatientReplay("p", "center_a", D0, d(end),
                         [(d(s), d(e), frozenset({"r"})) for s, e in alerts])  # fmt: skip


def test_detection_window_and_lead_time() -> None:
    episode = Episode("p", "rejection", "TCMR", onset=d(100), biopsy=d(130))
    assert detect({"p": patient([(110, 140)])}, [episode]).leads == (20,)
    # open when the window starts, 14 days before the signal onset: lead = 130 - 86
    assert detect({"p": patient([(60, 95)])}, [episode]).leads == (44,)
    assert detect({"p": patient([(60, 80)])}, [episode]).flagged == 0  # closed before window
    assert detect({"p": patient([(130, 140)])}, [episode]).flagged == 0  # biopsy day is too late
    assert detect({"p": patient([], end=120)}, [episode]).episodes == 0  # biopsy after follow-up


def test_alert_onsets_are_classified() -> None:
    episodes = [Episode("p", "rejection", "ABMR", d(100), d(130)),
                Episode("p", "injury", "bk_nephropathy", d(300), d(320))]  # fmt: skip
    replay_ = patient([(20, 25), (110, 120), (200, 210), (305, 330), (380, 390), (550, 560)],
                      end=600)  # fmt: skip
    counts = classify_onsets({"p": replay_}, episodes)
    assert counts == {"false alarm": 2, "rejection": 1, "after a proven episode": 2, "injury": 1}


def test_daily_counts() -> None:
    assert daily_counts([(d(1), d(3)), (d(2), d(5)), (d(-5), d(1))], d(0), d(5)) == [
        1, 1, 2, 1, 1, 0
    ]  # fmt: skip


def test_overdue_scoring_by_true_status() -> None:
    replay_ = patient([], end=200)
    replay_.overdue_dues = {d(30), d(61), d(91)}
    tests = {"p": {d(30): "missed", d(61): "quarantined", d(91): "done", d(183): "missed",
                   d(190): "missed"}}  # fmt: skip
    result = score_overdue({"p": replay_}, tests, tolerance_days=14)
    assert result.flagged == {"missed": 1, "quarantined": 1, "done": 1}
    assert (result.missed, result.missed_and_flagged) == (2, 1)  # day 190 window still open


def test_full_report_on_a_small_generated_dataset(tmp_path: Path) -> None:
    generate(replace(GeneratorConfig(), seed=3, n_patients=60), tmp_path, pdfs=False,
             note_text=False)  # fmt: skip
    generate_oncology(replace(OncologyConfig(), n_patients=40), 3, tmp_path)
    report = run(tmp_path / "ground_truth", tmp_path / "oncology", date(2026, 9, 30))
    assert report.startswith("# Surveillance engine")
    assert CAVEAT in report and "## Known gaps" in report
    assert "**Using surveillance and routine labs only. Kidney:" in report
    assert "## Threshold settings: sensitivity vs alert burden" in report
    assert "kidney: 0.5% watch / 1.0% high + trend (shipped)" in report
    assert "## Oncology config: same engine, different YAML" in report
