"""Replay the surveillance engine day by day over the synthetic history and score it against
the generator's ground truth.

`make surveillance-report` writes evals/reports/surveillance.md. For every patient the engine
is evaluated on every day its flags can change (equivalent to every calendar day, see
`engine.replay`), using only data collected up to that day. An "alert" is any flag of
medium or high severity; low-severity flags (overdue test, drug level outside its range) are
housekeeping and are counted in the worklist size but not as alerts.

Scored against data/ground_truth:
- rejection episodes with an alert active between (signal onset - 14 days) and the day before
  the biopsy, and the lead time (biopsy date - first alert day in that window);
- injury episodes (BK virus, CMV, acute tubular injury) alerted the same way: injury, not
  necessarily rejection;
- alert onsets outside any episode window, per 100 patient-years (false alarms);
- worklist size per day per center (alert burden);
- the overdue-test rule against the tests the generator marked as missed.

The generator draws extra "for-cause" labs a week before each biopsy, because a clinician
was already worried. Those labs would make detection look better than surveillance alone can
be, so detection is reported both with and without them.
"""

import argparse
import csv
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from statistics import median, quantiles

from txlens_backend.surveillance import csv_source
from txlens_backend.surveillance.config import (
    DAYS_PER_MONTH,
    Level,
    Severity,
    SurveillanceConfig,
    Threshold,
    load_configs,
)
from txlens_backend.surveillance.engine import replay
from txlens_backend.surveillance.model import Flag

ALERT = {Severity.HIGH, Severity.MEDIUM}
EARLY_TOLERANCE = timedelta(days=14)  # an alert this long before signal onset still counts
AFTER_BIOPSY = timedelta(days=30)  # alerts this soon after a biopsy belong to the episode
POST_EPISODE = timedelta(days=180)  # alerts in the months after a proven episode
BURDEN_FROM = date(2025, 1, 1)  # all transplants done by end of 2025: a steady-state window
CAVEAT = (
    "The synthetic data was generated with the same thresholds and signal shapes that the "
    "engine checks (for example, rejection pushes dd-cfDNA above 1% and creatinine up by "
    "10-90%). These results are therefore optimistic. They show that the engine, the replay "
    "and the scoring work end to end; they are not evidence of how well these rules would "
    "perform on real patients."
)


@dataclass(frozen=True)
class Episode:
    """A ground-truth episode: rejection (with type) or injury (with cause)."""

    patient_id: str
    kind: str
    label: str
    onset: date
    biopsy: date

    @property
    def window(self) -> tuple[date, date]:
        """Days on which an alert counts as catching this episode before its biopsy."""
        return self.onset - EARLY_TOLERANCE, self.biopsy - timedelta(days=1)


@dataclass
class PatientReplay:
    """What the engine showed for one patient over the whole replay."""

    patient_id: str
    tenant_id: str
    start: date
    end: date  # exclusive: surveillance ended (death, graft loss, recurrence) or data cutoff
    alerts: list[tuple[date, date, frozenset[str]]] = field(default_factory=list)
    any_flag: list[tuple[date, date]] = field(default_factory=list)
    overdue_dues: set[date] = field(default_factory=set)

    @property
    def years(self) -> float:
        """Patient-years under surveillance."""
        return (self.end - self.start).days / 365.25


def intervals(
    states: list[tuple[date, list[Flag]]], end: date, keep: Callable[[Flag], bool]
) -> list[tuple[date, date, frozenset[str]]]:
    """Maximal [start, end) stretches during which at least one kept flag is open, with the
    rules that opened the stretch."""
    found: list[tuple[date, date, frozenset[str]]] = []
    current: tuple[date, frozenset[str]] | None = None
    for i, (day, flags) in enumerate(states):
        kept = frozenset(f.rule_id for f in flags if keep(f))
        if kept and current is None:
            current = (day, kept)
        elif not kept and current is not None:
            found.append((current[0], day, current[1]))
            current = None
        if i == len(states) - 1 and current is not None:
            found.append((current[0], end, current[1]))
    return found


def replay_patient(
    config: SurveillanceConfig, patient: csv_source.CsvPatient, stop: date
) -> PatientReplay:
    """Replay one patient from the anchor date to the end of surveillance or `stop`."""
    series = patient.series
    end = min(series.end_date or stop, stop)
    states = list(replay(config, series, series.anchor_date, end))
    result = PatientReplay(series.patient_key, patient.tenant_id, series.anchor_date, end)
    if end <= series.anchor_date:
        return result
    result.alerts = intervals(states, end, lambda f: f.severity in ALERT)
    result.any_flag = [(s, e) for s, e, _ in intervals(states, end, lambda f: True)]
    result.overdue_dues = {f.evidence[0].day for _, flags in states for f in flags
                           if f.evidence and f.evidence[0].role == "due"}  # fmt: skip
    return result


def replay_cohort(
    config: SurveillanceConfig,
    patients: dict[str, csv_source.CsvPatient],
    follow_up: dict[str, date],
    cutoff: date,
) -> list[PatientReplay]:
    """Replay every patient up to the end of their follow-up (or the data cutoff)."""
    return [replay_patient(config, p, min(follow_up.get(key, cutoff), cutoff))
            for key, p in patients.items()]  # fmt: skip


# --- ground truth --------------------------------------------------------------------------


def rows(path: Path) -> list[dict[str, str]]:
    """All rows of a CSV."""
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def follow_up_ends(root: Path) -> dict[str, date]:
    """Last follow-up date per patient: the generator's data stops there."""
    return {r["patient_id"]: date.fromisoformat(r["last_follow_up_date"])
            for r in rows(root / "outcomes.csv")}  # fmt: skip


def load_episodes(truth: Path) -> list[Episode]:
    """Rejection and injury episodes from the transplant ground truth."""
    episodes = [Episode(r["patient_id"], "rejection", r["rejection_type"],
                        date.fromisoformat(r["signal_onset_date"]),
                        date.fromisoformat(r["biopsy_date"]))
                for r in rows(truth / "rejection_episodes.csv")]  # fmt: skip
    episodes += [Episode(r["patient_id"], "injury", r["cause"], date.fromisoformat(r["onset_date"]),
                         date.fromisoformat(r["biopsy_date"]))
                 for r in rows(truth / "injury_episodes.csv")]  # fmt: skip
    return episodes


def load_recurrences(oncology: Path) -> list[Episode]:
    """Oncology recurrences as episodes (clinical recurrence date in place of a biopsy)."""
    return [Episode(r["patient_id"], "recurrence", "recurrence",
                    date.fromisoformat(r["signal_onset_date"]),
                    date.fromisoformat(r["recurrence_date"]))
            for r in rows(oncology / "outcomes.csv") if r["recurrence_date"]]  # fmt: skip


def scheduled_tests(
    root: Path, patients: dict[str, csv_source.CsvPatient], skipped: dict[str, frozenset[str]]
) -> dict[str, dict[date, str]]:
    """Status of every scheduled test, keyed by patient and due date (as the engine computes
    it): "missed", "done", or the name of the `skipped` lab set its result was left out by."""
    status: dict[str, dict[date, str]] = defaultdict(dict)
    for r in rows(root / "surveillance_schedule.csv"):
        patient = patients.get(r["patient_id"])
        if patient is None:
            continue
        due = patient.series.anchor_date + timedelta(days=round(int(r["month"]) * DAYS_PER_MONTH))
        found = next((name for name, ids in skipped.items() if r["lab_id"] in ids), "done")
        status[r["patient_id"]][due] = "missed" if r["done"] == "false" else found
    return status


def for_cause_lab_ids(truth: Path) -> frozenset[str]:
    """Labs drawn because a clinician was already worried (around a biopsy)."""
    return frozenset(r["lab_id"] for r in rows(truth / "labs.csv") if r["reason"] == "for_cause")


# --- scoring --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Detection:
    """How many episodes had an alert before their biopsy, and how early."""

    episodes: int
    flagged: int
    leads: tuple[int, ...]

    @property
    def rate(self) -> float:
        """Share of episodes flagged."""
        return self.flagged / self.episodes if self.episodes else 0.0


def first_alert_day(replay_: PatientReplay, start: date, end: date) -> date | None:
    """The first day in [start, end] on which an alert is open."""
    days = [max(s, start) for s, e, _ in replay_.alerts if s <= end and e > start]
    return min(days, default=None)


def detect(replays: dict[str, PatientReplay], episodes: Iterable[Episode]) -> Detection:
    """Episodes with an alert open in their window, and lead times in days before the biopsy."""
    leads, total = [], 0
    for episode in episodes:
        replay_ = replays.get(episode.patient_id)
        if replay_ is None or episode.biopsy > replay_.end:
            continue
        total += 1
        day = first_alert_day(replay_, *episode.window)
        if day is not None:
            leads.append((episode.biopsy - day).days)
    return Detection(total, len(leads), tuple(leads))


def classify_onsets(replays: dict[str, PatientReplay], episodes: list[Episode]) -> Counter[str]:
    """Each alert onset as: rejection, injury, after a proven episode, or false alarm."""
    by_patient: dict[str, list[Episode]] = defaultdict(list)
    for episode in episodes:
        by_patient[episode.patient_id].append(episode)
    counts: Counter[str] = Counter()
    for patient_id, replay_ in replays.items():
        for start, _, _ in replay_.alerts:
            mine = by_patient.get(patient_id, [])
            near = [
                e for e in mine if e.onset - EARLY_TOLERANCE <= start <= e.biopsy + AFTER_BIOPSY
            ]
            if near:
                counts[near[0].kind] += 1
            elif any(e.biopsy < start <= e.biopsy + POST_EPISODE for e in mine):
                counts["after a proven episode"] += 1
            else:
                counts["false alarm"] += 1
    return counts


def onsets_by_rule(replays: Iterable[PatientReplay]) -> Counter[str]:
    """How often each rule opened an alert."""
    return Counter(rule for r in replays for _, _, rules in r.alerts for rule in rules)


@dataclass(frozen=True)
class Burden:
    """Daily worklist size in one center over the steady-state window."""

    tenant_id: str
    median_any: float
    p90_any: float
    median_alerts: float
    p90_alerts: float
    per_100_under_surveillance: float


def daily_counts(stretches: Iterable[tuple[date, date]], first: date, last: date) -> list[int]:
    """How many stretches are open on each day from first to last (inclusive)."""
    delta: Counter[date] = Counter()
    for start, end in stretches:
        delta[max(start, first)] += 1
        delta[max(end, first)] -= 1
    counts, open_now = [], 0
    day = first
    while day <= last:
        open_now += delta[day]
        counts.append(open_now)
        day += timedelta(days=1)
    return counts


def p90(values: list[int]) -> float:
    """90th percentile."""
    return quantiles(values, n=10)[-1] if len(values) > 1 else float(values[0] if values else 0)


def burden(replays: list[PatientReplay], first: date, last: date) -> list[Burden]:
    """Worklist size per day per center: any open flag, and alerts only."""
    by_center: dict[str, list[PatientReplay]] = defaultdict(list)
    for r in replays:
        by_center[r.tenant_id].append(r)
    result = []
    for tenant, mine in sorted(by_center.items()):
        any_flag = daily_counts(((s, e) for r in mine for s, e in r.any_flag), first, last)
        alerts = daily_counts(((s, e) for r in mine for s, e, _ in r.alerts), first, last)
        under = daily_counts(((r.start, r.end) for r in mine), first, last)
        ratio = median(100 * a / u for a, u in zip(any_flag, under, strict=True) if u)
        result.append(Burden(tenant, median(any_flag), p90(any_flag), median(alerts), p90(alerts),
                             ratio))  # fmt: skip
    return result


@dataclass(frozen=True)
class Overdue:
    """The overdue-test rule against what really happened to each scheduled test."""

    flagged: Counter[str]  # overdue flags by the test's true status
    missed: int  # truly missed tests whose window closed during follow-up
    missed_and_flagged: int


def score_overdue(
    replays: dict[str, PatientReplay], tests: dict[str, dict[date, str]], tolerance_days: int
) -> Overdue:
    """Why each overdue flag fired, and how many truly missed tests were flagged."""
    flagged: Counter[str] = Counter()
    missed = caught = 0
    for patient_id, replay_ in replays.items():
        mine = tests.get(patient_id, {})
        flagged.update(mine.get(due, "not scheduled") for due in replay_.overdue_dues)
        closed = {d for d, s in mine.items() if s == "missed"
                  and d + timedelta(days=tolerance_days) < replay_.end}  # fmt: skip
        missed += len(closed)
        caught += len(closed & replay_.overdue_dues)
    return Overdue(flagged, missed, caught)


# --- threshold variants ---------------------------------------------------------------------


def with_threshold_levels(config: SurveillanceConfig, levels: list[Level]) -> SurveillanceConfig:
    """The config with different dd-cfDNA threshold levels."""
    rules = tuple(r.model_copy(update={"levels": tuple(levels)}) if isinstance(r, Threshold)
                  else r for r in config.rules)  # fmt: skip
    return config.model_copy(update={"rules": rules})


def without_rule(config: SurveillanceConfig, rule_id: str) -> SurveillanceConfig:
    """The config with one rule removed."""
    return config.model_copy(update={"rules": tuple(r for r in config.rules if r.id != rule_id)})


def watch(at: float) -> Level:
    """A kidney "possible graft injury" level."""
    return Level(at_or_above=at, severity=Severity.MEDIUM, meaning="possible graft injury")


def high(at: float) -> Level:
    """A "high risk of rejection" level."""
    return Level(at_or_above=at, severity=Severity.HIGH, meaning="high risk of rejection")


def variants(configs: dict[str, SurveillanceConfig]) -> list[tuple[str, SurveillanceConfig]]:
    """Threshold settings to compare (the first per organ is the shipped config)."""
    kidney, heart = configs["kidney_transplant"], configs["heart_transplant"]
    return [
        ("kidney: 0.5% watch / 1.0% high + trend (shipped)", kidney),
        ("kidney: 1.0% high only + trend", with_threshold_levels(kidney, [high(1.0)])),
        ("kidney: 0.5% / 1.0%, no trend rule", without_rule(kidney, "dd_cfdna_rising")),
        ("kidney: 0.7% watch / 1.0% high + trend",
         with_threshold_levels(kidney, [watch(0.7), high(1.0)])),
        ("heart: 0.2% high + trend (shipped)", heart),
        ("heart: 0.15% high + trend", with_threshold_levels(heart, [high(0.15)])),
        ("heart: 0.25% high + trend", with_threshold_levels(heart, [high(0.25)])),
    ]  # fmt: skip


# --- report ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProgramResult:
    """Everything measured for one config on one lab set."""

    name: str
    patients: int
    years: float
    detection: Detection
    by_type: dict[str, Detection]
    injuries: dict[str, Detection]
    onsets: Counter[str]
    rules: Counter[str]
    burden: list[Burden]
    overdue: Overdue


def evaluate_program(
    name: str,
    config: SurveillanceConfig,
    patients: dict[str, csv_source.CsvPatient],
    episodes: list[Episode],
    tests: dict[str, dict[date, str]],
    follow_up: dict[str, date],
    cutoff: date,
) -> ProgramResult:
    """Replay one config and score it."""
    replays = {r.patient_id: r for r in replay_cohort(config, patients, follow_up, cutoff)}
    mine = [e for e in episodes if e.patient_id in replays]
    target = [e for e in mine if e.kind != "injury"]
    labels = sorted({e.label for e in mine})
    schedule = config.schedule
    return ProgramResult(
        name=name, patients=len(replays), years=sum(r.years for r in replays.values()),
        detection=detect(replays, target),
        by_type={t: detect(replays, [e for e in target if e.label == t]) for t in labels
                 if any(e.label == t for e in target)},
        injuries={c: detect(replays, [e for e in mine if e.kind == "injury" and e.label == c])
                  for c in labels if any(e.kind == "injury" and e.label == c for e in mine)},
        onsets=classify_onsets(replays, mine), rules=onsets_by_rule(replays.values()),
        burden=burden(list(replays.values()), BURDEN_FROM, cutoff),
        overdue=score_overdue(replays, tests, schedule.tolerance_days if schedule else 0),
    )  # fmt: skip


def pct(n: int, d: int) -> str:
    """'n of d (p%)'."""
    return f"{n:,} of {d:,} ({100 * n / d:.0f}%)" if d else "0 of 0"


def lead(detection: Detection) -> str:
    """Median lead time with its interquartile range."""
    leads = list(detection.leads)
    if not leads:
        return "-"
    if len(leads) == 1:
        return f"{leads[0]} days"
    q1, _, q3 = quantiles(leads, n=4)
    return f"{median(leads):.0f} days (IQR {q1:.0f}-{q3:.0f})"


def per_100_years(n: int, years: float) -> str:
    """A count per 100 patient-years."""
    return f"{100 * n / years:.1f}" if years else "-"


def program(r: ProgramResult) -> str:
    """'Kidney' from 'Kidney, surveillance and routine labs only'."""
    return r.name.split(",")[0]


def table(header: list[str], body: Iterable[list[str]]) -> list[str]:
    """A markdown table."""
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return lines + ["| " + " | ".join(row) + " |" for row in body]


def headline(surveillance: list[ProgramResult]) -> list[str]:
    """Title, provenance, method, caveat and the bold headline."""
    summary = " ".join(
        f"{program(r)}: {pct(r.detection.flagged, r.detection.episodes)} rejection episodes "
        f"flagged before the biopsy, median lead {lead(r.detection)}, "
        f"{per_100_years(r.onsets['false alarm'], r.years)} false alarms per 100 patient-years."
        for r in surveillance
    )
    return [
        "# Surveillance engine: day-by-day replay vs ground truth", "",
        f"Generated {date.today().isoformat()} by `make surveillance-report` "
        "(evals/src/txlens_evals/surveillance_replay.py). Synthetic data only.", "",
        "The engine is replayed over each patient's whole follow-up, seeing only data collected "
        "up to each day. An **alert** is a medium or high severity flag; low-severity flags "
        "(overdue test, tacrolimus outside its range) only count towards the worklist size. "
        "The engine supports clinical decisions; it does not diagnose.", "",
        f"> **Read with care:** {CAVEAT}", "",
        f"**Using surveillance and routine labs only. {summary}**",
    ]  # fmt: skip


def detection_section(results: list[ProgramResult], surveillance: list[ProgramResult]) -> list[str]:
    """Rejection detection overall and by type."""
    out = ["", "## Rejection episodes flagged before the biopsy", "",
           "Window: from 14 days before the signal onset to the day before the biopsy. "
           "Lead time = biopsy date - first day an alert was open in that window.", ""]  # fmt: skip
    out += table(["Program, labs used", "Patients", "Episodes flagged", "Median lead (IQR)"],
                 ([r.name, f"{r.patients:,}", pct(r.detection.flagged, r.detection.episodes),
                   lead(r.detection)] for r in results))  # fmt: skip
    out += ["", "For-cause labs are drawn a week before the biopsy because a clinician was "
            "already worried; the rows without them show what scheduled surveillance alone finds.",
            "", "### By rejection type (surveillance and routine labs only)", ""]  # fmt: skip
    out += table(["Program", "Type", "Flagged", "Median lead"],
                 ([program(r), t, pct(d.flagged, d.episodes), lead(d)]
                  for r in surveillance for t, d in r.by_type.items()))  # fmt: skip
    return out


def injury_section(surveillance: list[ProgramResult]) -> list[str]:
    """Injury episodes: flagged, but not necessarily rejection."""
    out = ["", "## Injury episodes: injury, not necessarily rejection", "",
           "BK virus nephropathy, CMV infection and acute tubular injury also raise dd-cfDNA and "
           "creatinine. Flagging them is expected: the marker shows graft injury, not its cause. "
           "The biopsy tells them apart.", ""]  # fmt: skip
    return out + table(["Program", "Cause", "Flagged before biopsy", "Median lead"],
                       ([program(r), c.replace("_", " "), pct(d.flagged, d.episodes), lead(d)]
                        for r in surveillance for c, d in r.injuries.items()))  # fmt: skip


def onset_section(results: list[ProgramResult], surveillance: list[ProgramResult]) -> list[str]:
    """Alert onsets by what they turned out to be, and by rule."""
    out = ["", "## Alert onsets and false alarms", "",
           "Each time a patient goes from no alert to an alert counts once. Onsets within an "
           "episode window (to 30 days after its biopsy) belong to that episode; onsets up to "
           "180 days after a proven episode are follow-up of known injury; the rest are false "
           "alarms.", ""]  # fmt: skip
    out += table(["Program, labs used", "Patient-years", "Rejection", "Injury",
                  "After a proven episode", "False alarms", "False alarms per 100 patient-years"],
                 ([r.name, f"{r.years:,.0f}", f"{r.onsets['rejection']:,}",
                   f"{r.onsets['injury']:,}", f"{r.onsets['after a proven episode']:,}",
                   f"{r.onsets['false alarm']:,}",
                   per_100_years(r.onsets["false alarm"], r.years)] for r in results))  # fmt: skip
    out += ["", "### Which rules opened alerts (surveillance and routine labs only)", ""]
    return out + table(["Program", "Rule", "Alert onsets"],
                       ([program(r), rule, f"{n:,}"]
                        for r in surveillance for rule, n in r.rules.most_common()))  # fmt: skip


def burden_section(surveillance: list[ProgramResult]) -> list[str]:
    """Worklist size per day per center."""
    out = ["", f"## Alert burden: worklist size per day ({BURDEN_FROM.isoformat()} to data "
           "cutoff, by center)", "",
           "\"Any flag\" includes low-severity housekeeping flags (overdue test, tacrolimus "
           "range). Flags here clear only when their rule stops firing; clinicians "
           "resolving flags would shrink the list further.", ""]  # fmt: skip
    return out + table(["Program", "Center", "Any flag: median / p90", "Alerts: median / p90",
                        "Any flag per 100 patients under surveillance (median)"],
                       ([program(r), b.tenant_id, f"{b.median_any:.0f} / {b.p90_any:.0f}",
                         f"{b.median_alerts:.0f} / {b.p90_alerts:.0f}",
                         f"{b.per_100_under_surveillance:.0f}"]
                        for r in surveillance for b in r.burden))  # fmt: skip


def overdue_row(r: ProgramResult) -> list[str]:
    """One program's overdue flags by what really happened to the test."""
    flagged, total = r.overdue.flagged, sum(r.overdue.flagged.values())
    other = total - flagged["missed"] - flagged["quarantined"] - flagged["for cause"]
    return [program(r), f"{total:,}", pct(flagged["missed"], total), f"{flagged['quarantined']:,}",
            f"{flagged['for cause']:,}", f"{other:,}",
            pct(r.overdue.missed_and_flagged, r.overdue.missed)]  # fmt: skip


def overdue_section(surveillance: list[ProgramResult]) -> list[str]:
    """The overdue-test rule against what really happened to each test."""
    out = ["", "## Overdue-test rule vs what really happened to each test", "",
           "An overdue flag for a test that was done means its result never reached the engine: "
           "the pipeline quarantined it (bad date or missing value in the lab feed), or the "
           "draw was also a for-cause draw, which this lab set leaves out.", ""]  # fmt: skip
    return out + table(["Program", "Overdue flags", "Truly missed", "Result quarantined",
                        "Drawn for cause", "Other", "Missed tests flagged"],
                       (overdue_row(r) for r in surveillance))  # fmt: skip


def comparison_row(label: str, r: ProgramResult) -> list[str]:
    """One threshold setting: sensitivity vs burden."""
    injured = sum(d.episodes for d in r.injuries.values())
    injured_flagged = sum(d.flagged for d in r.injuries.values())
    daily = sum(b.median_alerts for b in r.burden)
    return [label, pct(r.detection.flagged, r.detection.episodes), lead(r.detection),
            pct(injured_flagged, injured), per_100_years(r.onsets["false alarm"], r.years),
            f"{daily:.0f}"]  # fmt: skip


def comparison_section(comparison: list[tuple[str, ProgramResult]]) -> list[str]:
    """Threshold settings side by side."""
    out = ["", "## Threshold settings: sensitivity vs alert burden", "",
           "Surveillance and routine labs only. Burden = median number of patients with an "
           "alert per day, all centers together.", ""]  # fmt: skip
    return out + table(["Setting", "Rejections flagged before biopsy", "Median lead",
                        "Injury episodes flagged", "False alarms per 100 patient-years",
                        "Patients with an alert per day (median)"],
                       (comparison_row(label, r) for label, r in comparison))  # fmt: skip


def oncology_section(onc: ProgramResult) -> list[str]:
    """The oncology config on its own cohort."""
    flagged, total = onc.overdue.flagged["missed"], sum(onc.overdue.flagged.values())
    return [
        "", "## Oncology config: same engine, different YAML", "",
        "A small synthetic cohort after curative cancer treatment, with a generic tumor DNA "
        "marker tested every 3 months (config `oncology_tumor_dna`). No code was changed to "
        "run it.", "",
        f"- Patients: {onc.patients:,} ({onc.years:,.0f} patient-years)",
        "- Recurrences flagged before clinical recurrence: "
        f"{pct(onc.detection.flagged, onc.detection.episodes)}, median lead {lead(onc.detection)}",
        f"- False alarms: {onc.onsets['false alarm']:,} "
        f"({per_100_years(onc.onsets['false alarm'], onc.years)} per 100 patient-years)",
        f"- Overdue flags: {total:,}, of which {pct(flagged, total)} for a truly missed test; "
        f"{pct(onc.overdue.missed_and_flagged, onc.overdue.missed)} missed tests flagged", "",
        "With testing every 3 months and a signal that starts 30-120 days before recurrence, "
        "some recurrences have no test inside the signal window at all; those cannot be caught "
        "by any threshold.",
    ]  # fmt: skip


KNOWN_GAPS = [
    "", "## Known gaps", "",
    f"- **Optimistic by construction.** {CAVEAT}",
    "- Flags are cleared only when their rule stops firing. Clinicians acknowledging and "
    "resolving flags is not simulated, so the worklist sizes are an upper bound.",
    "- The replay reads the ground-truth CSVs. A database test checks that the database path "
    "gives identical flags for the same data (backend/tests/test_db_surveillance.py).",
    "- Tacrolimus levels in the generator are noisy by design, so the low-severity range flag "
    "fires often. It adds to the worklist but not to the alert counts.",
    "- Heart transplant rejection is also screened by protocol biopsies, which are not scored "
    "here.", "",
]  # fmt: skip


def render(
    results: list[ProgramResult],
    comparison: list[tuple[str, ProgramResult]],
    oncology: ProgramResult | None,
) -> str:
    """The markdown report."""
    surveillance = [r for r in results if "surveillance" in r.name]
    out = headline(surveillance)
    out += detection_section(results, surveillance) + injury_section(surveillance)
    out += onset_section(results, surveillance) + burden_section(surveillance)
    out += overdue_section(surveillance) + comparison_section(comparison)
    if oncology is not None:
        out += oncology_section(oncology)
    return "\n".join(out + KNOWN_GAPS)


def run(truth: Path, oncology_dir: Path | None, cutoff: date) -> str:
    """Load data, replay every program and setting, and render the report."""
    configs = load_configs()
    episodes = load_episodes(truth)
    follow_up = follow_up_ends(truth)
    unloadable = csv_source.unloadable_lab_ids(truth)
    for_cause = for_cause_lab_ids(truth)
    lab_sets = {"all labs": {"quarantined": unloadable},
                "surveillance and routine labs only": {"quarantined": unloadable,
                                                       "for cause": for_cause}}  # fmt: skip
    results, cohorts = [], {}
    for config_id, organ in (("kidney_transplant", "Kidney"), ("heart_transplant", "Heart")):
        config = configs[config_id]
        for label, skipped in lab_sets.items():
            patients = csv_source.load(
                truth, config, skip_lab_ids=frozenset().union(*skipped.values())
            )
            tests = scheduled_tests(truth, patients, skipped)
            results.append(evaluate_program(f"{organ}, {label}", config, patients, episodes,
                                            tests, follow_up, cutoff))  # fmt: skip
            cohorts[(config.program, label)] = (patients, tests)
    comparison = []
    for label, config in variants(configs):
        patients, tests = cohorts[(config.program, "surveillance and routine labs only")]
        comparison.append((label, evaluate_program(label, config, patients, episodes, tests,
                                                   follow_up, cutoff)))  # fmt: skip
    oncology = None
    if oncology_dir is not None and (oncology_dir / "patients.csv").exists():
        config = configs["oncology_tumor_dna"]
        patients = csv_source.load(oncology_dir, config)
        oncology = evaluate_program("Oncology", config, patients, load_recurrences(oncology_dir),
                                    scheduled_tests(oncology_dir, patients, {}),
                                    follow_up_ends(oncology_dir), cutoff)  # fmt: skip
    return render(results, comparison, oncology)


def main(argv: list[str] | None = None) -> int:
    """Write the report."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--truth", type=Path, default=Path("data/ground_truth"))
    parser.add_argument("--oncology", type=Path, default=Path("data/oncology"))
    parser.add_argument("--cutoff", type=date.fromisoformat, default=date(2026, 9, 30))
    parser.add_argument("--out", type=Path, default=Path("evals/reports/surveillance.md"))
    args = parser.parse_args(argv)
    report = run(args.truth, args.oncology, args.cutoff)
    args.out.write_text(report)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
