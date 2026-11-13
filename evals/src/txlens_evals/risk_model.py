"""Rejection-risk model evaluation: `make risk-report` -> evals/reports/risk_model.md (+ .json).

Reads every center as the app login (RLS on), rebuilds the visit dataset, reruns the same
deterministic development as `make risk-train`, and reports:

1. Data, and OMOP labels against the generator's ground truth.
2. Which draws became visits: how many kept visits were really for-cause.
3. Model choice by cross-validation.
4. Discrimination and calibration on the internal and external (center C) tests.
5. The model against the rule engine at the same alert burden.
6. Decision curves.
7. What the models use.
8. Sensitivity analyses: borderline as rejection, ground-truth labels, (a) without draws
   shortly before a biopsy, (b) visits picked by the true draw reason, by organ.
9. Fairness by age band and sex.
10. Drift (PSI) between centers and over time, and in nightly scoring.
11. The registered champion against this report.

Trained on synthetic data, not clinically validated, decision support only.
"""

import argparse
import csv
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sqlalchemy import Engine, text

from txlens_backend.config import get_settings
from txlens_backend.db import check_role_is_rls_bound, make_engine, tenant_transaction
from txlens_backend.db_setup import center_tenants
from txlens_backend.risk import LABEL_TEXT
from txlens_backend.risk.dataset import COLUMNS, load_records, visit_rows
from txlens_backend.risk.develop import (
    EXTERNAL_CENTER,
    TRAIN_CENTERS,
    Development,
    develop,
    score_column,
)
from txlens_backend.risk.evaluate import MIN_EVENTS, Summary, summarize
from txlens_backend.risk.explain import contributions
from txlens_backend.risk.features import FEATURE_NAMES, FEATURES
from txlens_backend.risk.labels import HORIZON
from txlens_backend.risk.metrics import (
    auprc,
    auroc,
    drift_band,
    net_benefit,
    psi,
    threshold_for_rate,
)
from txlens_backend.risk.models import Kind
from txlens_backend.risk.record import PatientRecord
from txlens_backend.surveillance.config import SurveillanceConfig
from txlens_backend.surveillance.run import transplant_configs
from txlens_evals.stats import write_headline

REJECTION_TYPES = {"TCMR", "ABMR", "mixed"}
NAMES = {Kind.LOGISTIC: "Logistic regression", Kind.LIGHTGBM: "LightGBM"}
DCA_THRESHOLDS = (0.01, 0.02, 0.03, 0.05, 0.10, 0.20)
LABELS = {f.name: f.label for f in FEATURES}
Lines = list[str]


# --- data -------------------------------------------------------------------------------


@dataclass
class Loaded:
    """Every center's records and visits, with each patient's ground-truth id."""

    records: list[tuple[PatientRecord, SurveillanceConfig]]
    frame: pd.DataFrame
    patient_of: dict[str, str]  # visit key -> ground-truth patient_id


def load(engine: Engine, as_of: date, truth: Path) -> Loaded:
    """Records and visits of every center (RLS on), joined to the truth by MRN."""
    with (truth / "patients.csv").open() as f:
        by_mrn = {(p["tenant_id"], p["mrn"]): p["patient_id"] for p in csv.DictReader(f)}
    records, patient_of = [], {}
    for tenant in center_tenants():
        with tenant_transaction(engine, tenant) as conn:
            records += load_records(conn, tenant, transplant_configs(), as_of)
            for person_id, mrn in conn.execute(
                text("SELECT person_id, mrn FROM app.patient_identity")
            ):
                patient_of[f"{tenant}:{person_id}"] = by_mrn[(tenant, mrn)]
    rows = [row for record, config in records for row in visit_rows(record, config, as_of)]
    return Loaded(records, pd.DataFrame(rows, columns=COLUMNS), patient_of)


@dataclass
class Truth:
    """What the generator knows: rejection biopsies, outcomes and why each draw was taken."""

    rejections: dict[str, list[date]]
    borderline: dict[str, list[date]]
    follow_up: dict[str, date]
    ends: dict[str, date | None]
    draw_reasons: dict[tuple[str, date], set[str]]
    surveillance_draws: dict[str, list[date]]

    @classmethod
    def read(cls, folder: Path) -> "Truth":
        def rows(name: str) -> list[dict[str, str]]:
            with (folder / f"{name}.csv").open() as f:
                return list(csv.DictReader(f))

        rejections: dict[str, list[date]] = defaultdict(list)
        borderline: dict[str, list[date]] = defaultdict(list)
        for e in rows("rejection_episodes"):
            target = rejections if e["rejection_type"] in REJECTION_TYPES else borderline
            target[e["patient_id"]].append(date.fromisoformat(e["biopsy_date"]))
        follow_up, ends = {}, {}
        for o in rows("outcomes"):
            follow_up[o["patient_id"]] = date.fromisoformat(o["last_follow_up_date"])
            found = [date.fromisoformat(o[k]) for k in ("graft_loss_date", "death_date") if o[k]]
            ends[o["patient_id"]] = min(found, default=None)
        reasons: dict[tuple[str, date], set[str]] = defaultdict(set)
        surveillance: dict[str, list[date]] = defaultdict(list)
        for lab in rows("labs"):
            if lab["lab"] != "dd_cfdna":
                continue
            day = datetime.fromisoformat(lab["collected_at"]).date()
            reasons[(lab["patient_id"], day)].add(lab["reason"])
            if lab["reason"] == "surveillance":
                surveillance[lab["patient_id"]].append(day)
        return cls(rejections, borderline, follow_up, ends, reasons, surveillance)

    def label(self, patient: str, visit: date, as_of: date) -> float:
        """The 90-day label from the truth (NaN when the window is not fully observed)."""
        end = visit + HORIZON
        if any(visit < b <= end for b in self.rejections.get(patient, [])):
            return 1.0
        if min(self.follow_up[patient], as_of) < end:
            return float("nan")
        stop = self.ends.get(patient)
        return float("nan") if stop is not None and stop <= end else 0.0


# --- formatting -------------------------------------------------------------------------


def pct(value: float, digits: int = 1) -> str:
    return "n/a" if value != value else f"{100 * value:.{digits}f}%"


def table(header: list[str], body: Iterable[list[str]]) -> Lines:
    return [
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
        *("| " + " | ".join(row) + " |" for row in body),
    ]


def count(n: int) -> str:
    """Counts of 1-10 are shown as "<11", as in the app."""
    return "<11" if 0 < n < MIN_EVENTS else str(n)


# --- 1. data and labels -----------------------------------------------------------------


def data_section(loaded: Loaded, truth: Truth, as_of: date) -> tuple[Lines, dict[str, Any]]:
    frame = loaded.frame
    body: list[list[str]] = []
    for tenant, part in frame.groupby("tenant", sort=True):
        role = "external test" if tenant == EXTERNAL_CENTER else "development + internal test"
        lab = part[part["label"].notna()]
        body.append(
            [
                str(tenant),
                role,
                str(part["key"].nunique()),
                str(len(part)),
                str(len(lab)),
                str(int(lab["label"].sum())),
                pct(float(lab["label"].mean())),
            ]
        )
    gt = np.array(
        [
            truth.label(loaded.patient_of[k], v, as_of)
            for k, v in zip(frame["key"], frame["visit"], strict=True)
        ]
    )
    omop = frame["label"].to_numpy(dtype=float)
    both = ~np.isnan(gt) & ~np.isnan(omop)
    tp = int(((gt == 1) & (omop == 1) & both).sum())
    fn = int(((gt == 1) & (omop == 0) & both).sum())
    fp = int(((gt == 0) & (omop == 1) & both).sum())
    # Biopsy level: true rejection biopsies (up to the extract date) that OMOP labels as such.
    found = {
        (r.key, x.biopsy_day)
        for r, _ in loaded.records
        for x in r.results
        if x.finding.value == "rejection"
    }
    keys = {loaded.patient_of[r.key]: r.key for r, _ in loaded.records}
    true_biopsies = [
        (keys[p], b)
        for p, days in truth.rejections.items()
        if p in keys
        for b in days
        if b <= as_of
    ]
    hit = sum(1 for b in true_biopsies if b in found)
    true_set = set(true_biopsies)
    extra = sum(1 for b in found if b not in true_set)
    true_border = sum(
        1 for p, days in truth.borderline.items() if p in keys for b in days if b <= as_of
    )
    found_border = sum(
        1 for r, _ in loaded.records for x in r.results if x.finding.value == "borderline"
    )
    lines = [
        "One row per surveillance visit (the first dd-cfDNA draw in each schedule window). A "
        "visit is labelled when its 90-day window is fully observed.",
        "",
        *table(
            ["Center", "Role", "Patients", "Visits", "Labelled", "Rejection visits", "Prevalence"],
            body,
        ),
        "",
        "**OMOP labels against the generator's truth.** Rejection results reach OMOP from "
        "approved pathology reports and from clinic-note NLP (a note result counts when a "
        "biopsy precedes it by at most 14 days). Reports still waiting in the review queue are "
        "missing from OMOP, so the notes carry most of the labels.",
        "",
        f"- Rejection biopsies (TCMR, ABMR, mixed) up to the extract date: {len(true_biopsies)} "
        f"in the truth, {hit} found in OMOP ({pct(hit / max(len(true_biopsies), 1))}); "
        f"{extra} OMOP rejection biopsies are not in the truth.",
        f"- Borderline biopsies: {true_border} in the truth, {found_border} found in OMOP "
        "(the note NLP rarely records borderline).",
        f"- Visits labelled by both: OMOP finds {tp} of {tp + fn} truly positive visits "
        f"({pct(tp / max(tp + fn, 1))}); {fp} OMOP-positive visits are negative in the truth.",
    ]
    head = {
        "visits": len(frame),
        "patients": int(frame["key"].nunique()),
        "labelled": int(np.sum(~np.isnan(omop))),
        "events": int(np.nansum(omop)),
        "omop_biopsy_recall": hit / max(len(true_biopsies), 1),
        "omop_visit_label_recall": tp / max(tp + fn, 1),
    }
    return lines, head


# --- 2. visits --------------------------------------------------------------------------


def visit_reason(truth: Truth, patient: str, day: date) -> str:
    reasons = truth.draw_reasons.get((patient, day), set())
    if "surveillance" in reasons:
        return "surveillance"
    return "for_cause" if "for_cause" in reasons else ("routine" if reasons else "unknown")


def visits_section(loaded: Loaded, truth: Truth) -> tuple[Lines, dict[str, Any]]:
    frame = loaded.frame.assign(
        reason=[
            visit_reason(truth, loaded.patient_of[k], v)
            for k, v in zip(loaded.frame["key"], loaded.frame["visit"], strict=True)
        ]
    )
    body = []
    for organ, part in [("all", frame), *frame.groupby("organ", sort=True)]:
        shares = part["reason"].value_counts(normalize=True)
        pos = part[part["label"] == 1]
        body.append(
            [
                str(organ),
                str(len(part)),
                pct(shares.get("surveillance", 0.0)),
                pct(shares.get("for_cause", 0.0)),
                pct(float((pos["reason"] == "for_cause").mean()) if len(pos) else float("nan")),
            ]
        )
    kept = {(loaded.patient_of[k], v) for k, v in zip(frame["key"], frame["visit"], strict=True)}
    in_scope = {loaded.patient_of[r.key] for r, _ in loaded.records}
    scheduled = [
        (p, d) for p, days in truth.surveillance_draws.items() if p in in_scope for d in days
    ]
    captured = sum(1 for x in scheduled if x in kept)
    for_cause = float((frame["reason"] == "for_cause").mean())
    lines = [
        "The draw reason never reaches OMOP, so the schedule rule cannot see it. It does not "
        "drop a draw because a biopsy follows (that is future information). The generator's "
        "reason for each kept draw:",
        "",
        *table(
            [
                "Organ",
                "Visits",
                "Truly scheduled",
                "Truly for-cause",
                "For-cause among rejection visits",
            ],
            body,
        ),
        "",
        f"The rule keeps {captured} of {len(scheduled)} truly scheduled draws "
        f"({pct(captured / max(len(scheduled), 1))}); a scheduled draw is missed when an earlier "
        "draw (often for-cause) falls in the same window. Heart protocol biopsies coincide with "
        "scheduled months, so their pre-biopsy draws are scheduled draws.",
    ]
    return lines, {
        "for_cause_share": for_cause,
        "scheduled_captured": captured / max(len(scheduled), 1),
    }


# --- 3-4. selection, discrimination, calibration ----------------------------------------


def selection_section(dev: Development) -> Lines:
    body = [
        [
            NAMES[r.kind],
            ", ".join(f"{k}={v}" for k, v in r.params.items()),
            f"{r.auprc:.3f}",
            f"{r.auroc:.3f}",
            "chosen" if dict(dev.best[r.kind]) == dict(r.params) else "",
        ]
        for r in dev.cv
    ]
    return [
        "Five-fold patient-grouped cross-validation on the development patients (centers A+B, "
        "80%). Rule fixed in advance: LightGBM becomes champion only if its best mean AUPRC "
        "beats logistic regression's by at least 0.01.",
        "",
        *table(["Model", "Hyperparameters", "CV AUPRC", "CV AUROC", ""], body),
        "",
        f"Champion: **{NAMES[dev.champion]}**.",
    ]


def scores_section(summary: Summary) -> Lines:
    body = []
    for split, scores in (
        ("Internal (A+B, 20%)", summary.internal),
        ("External (C)", summary.external),
    ):
        for kind, s in scores.items():
            star = " (champion)" if kind is summary.champion else ""
            body.append(
                [
                    split,
                    NAMES[kind] + star,
                    f"{s.n_visits} / {s.n_patients}",
                    str(s.n_events),
                    s.auroc.text(),
                    s.auprc.text(),
                    f"{s.prevalence:.3f}",
                    s.brier.text(4),
                    f"{s.calibration_intercept:.2f}",
                    f"{s.calibration_slope:.2f}",
                ]
            )
    return [
        "95% intervals resample patients (1,000 resamples). AUPRC should be read against the "
        "prevalence (a random score's AUPRC). Calibration: intercept 0 and slope 1 are perfect; "
        "a slope below 1 means the scores are too extreme.",
        "",
        *table(
            [
                "Test",
                "Model",
                "Visits / patients",
                "Rejection visits",
                "AUROC",
                "AUPRC",
                "Prevalence",
                "Brier",
                "Cal. intercept",
                "Cal. slope",
            ],
            body,
        ),
        "",
        "![Calibration](figures/risk_calibration.png)",
    ]


def calibration_figure(dev: Development, out: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.5), sharey=True)
    for ax, (title, part) in zip(
        axes, (("Internal test", dev.internal), ("External test (C)", dev.external)), strict=True
    ):
        top = 0.0
        for kind, marker in ((Kind.LOGISTIC, "o"), (Kind.LIGHTGBM, "s")):
            p = part[score_column(kind)].to_numpy()
            y = part[dev.label].to_numpy(dtype=float)
            bins = pd.qcut(p, 10, labels=False, duplicates="drop")
            mean_p = pd.Series(p).groupby(bins).mean()
            mean_y = pd.Series(y).groupby(bins).mean()
            ax.plot(mean_p, mean_y, marker=marker, label=NAMES[kind])
            top = max(top, float(mean_p.max()), float(mean_y.max()))
        ax.plot([0, top], [0, top], "k--", linewidth=1, label="perfect")
        ax.set_title(title)
        ax.set_xlabel("Predicted risk (decile mean)")
        ax.legend()
    axes[0].set_ylabel("Observed rejection rate")
    fig.suptitle("Calibration (synthetic data, not clinically validated)")
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)


# --- 5. rule engine comparison ----------------------------------------------------------


def episodes_caught(part: pd.DataFrame, alert: np.ndarray) -> tuple[int, int]:
    """(rejection episodes with an alert at a visit in the 90 days before, all episodes)."""
    data = part.assign(alert=alert)
    pos = data[data["next_rejection"].notna()]
    groups = pos.groupby(["key", "next_rejection"])["alert"].any()
    return int(groups.sum()), len(groups)


def burden_section(dev: Development, summary: Summary) -> tuple[Lines, dict[str, Any]]:
    kind = dev.champion
    oof = dev.development[score_column(kind)].to_numpy()
    fixed = threshold_for_rate(oof, float(dev.development["rule_alert"].mean()))
    body, episode_rows, fixed_rows = [], [], []
    for name, part, b in (
        ("Internal", dev.internal, summary.burden_internal),
        ("External (C)", dev.external, summary.burden_external),
    ):
        body.append(
            [
                name,
                pct(b.alert_rate),
                b.rule_sensitivity.text(),
                b.model_sensitivity.text(),
                b.difference.text(),
                pct(b.rule_ppv),
                pct(b.model_ppv),
            ]
        )
        p = part[score_column(kind)].to_numpy()
        same = p >= threshold_for_rate(p, b.alert_rate)
        r_hit, n = episodes_caught(part, part["rule_alert"].to_numpy(dtype=bool))
        m_hit, _ = episodes_caught(part, same)
        episode_rows.append(
            [
                name,
                str(n),
                f"{r_hit} ({pct(r_hit / max(n, 1))})",
                f"{m_hit} ({pct(m_hit / max(n, 1))})",
            ]
        )
        y = part[dev.label].to_numpy(dtype=float)
        at_fixed = p >= fixed
        fixed_rows.append(
            [
                name,
                pct(float(at_fixed.mean())),
                pct(float(at_fixed[y == 1].mean())),
                pct(b.alert_rate),
                pct(b.rule_sensitivity.value),
            ]
        )
    lines = [
        "The rule engine alerts when any medium or high flag is active on the visit day (the "
        "Step 1.6 definition). The model alerts on the same share of visits: its highest scores. "
        "Same alert burden, so the question is which rejections each one catches. The "
        "difference is paired by patient.",
        "",
        *table(
            [
                "Test",
                "Alert rate",
                "Rejection visits caught: rules",
                "Model",
                "Difference (model - rules)",
                "PPV rules",
                "PPV model",
            ],
            body,
        ),
        "",
        "**Episodes:** a rejection episode counts as caught when at least one visit in the 90 "
        "days before its biopsy alerted.",
        "",
        *table(["Test", "Episodes", "Caught by rules", "Caught by model"], episode_rows),
        "",
        "**A threshold fixed in advance.** Matching the rate on the test set uses the test "
        "set; in practice the cut-off is set beforehand. Here it is set on the development "
        "out-of-fold scores at the development alert rate, then applied unchanged:",
        "",
        *table(
            ["Test", "Model alert rate", "Model catches", "Rule alert rate", "Rules catch"],
            fixed_rows,
        ),
    ]
    b = summary.burden_external
    return lines, {
        "external_rule_sensitivity": b.rule_sensitivity.as_dict(),
        "external_model_sensitivity": b.model_sensitivity.as_dict(),
        "external_difference": b.difference.as_dict(),
        "external_alert_rate": b.alert_rate,
    }


# --- 6. decision curves -----------------------------------------------------------------


def dca_section(dev: Development, out: Path) -> Lines:
    part = dev.external
    y = part[dev.label].to_numpy(dtype=float)
    rule = part["rule_alert"].to_numpy(dtype=bool)
    scores = {k: part[score_column(k)].to_numpy() for k in Kind}

    def model_curve(kind: Kind) -> Callable[[float], float]:
        return lambda t: net_benefit(y, scores[kind] >= t, t)

    curves: dict[str, Callable[[float], float]] = {NAMES[k]: model_curve(k) for k in Kind}
    curves["Rule engine (fixed alerts)"] = lambda t: net_benefit(y, rule, t)
    curves["Biopsy everyone"] = lambda t: net_benefit(y, np.ones_like(rule), t)
    body = [[f"{t:.0%}", *(f"{curves[c](t):.4f}" for c in curves)] for t in DCA_THRESHOLDS]
    grid = np.linspace(0.005, 0.2, 60)
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for name, curve in curves.items():
        ax.plot(grid, [curve(t) for t in grid], label=name)
    ax.axhline(0, color="k", linewidth=1, label="Biopsy no one")
    ax.set_ylim(-0.005, max(0.02, float(y.mean()) * 1.2))
    ax.set_xlabel("Risk threshold for acting")
    ax.set_ylabel("Net benefit")
    ax.set_title("Decision curves, external test (synthetic data)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return [
        "Net benefit = true alerts/n - false alerts/n x t/(1-t), where t is the risk at which a "
        'clinician would act (e.g. order a biopsy). Higher is better; "biopsy no one" is 0. '
        "External test (center C).",
        "",
        *table(["Threshold", *curves], body),
        "",
        "![Decision curves](figures/risk_dca.png)",
    ]


# --- 7. what the models use -------------------------------------------------------------


def importance_section(dev: Development) -> Lines:
    part = dev.external
    rows = []
    means = {}
    for kind in Kind:
        values, _ = contributions(dev.models[f"external_{kind.value}"], part)
        means[kind] = np.abs(values).mean(axis=0)
    for i, name in sorted(enumerate(FEATURE_NAMES), key=lambda x: -means[dev.champion][x[0]]):
        rows.append(
            [LABELS[name], f"{means[Kind.LOGISTIC][i]:.3f}", f"{means[Kind.LIGHTGBM][i]:.3f}"]
        )
    return [
        "Mean absolute contribution to the log-odds on the external test (logistic regression: "
        "coefficient x standardised value; LightGBM: TreeSHAP). The generator gives tacrolimus "
        "variability, donor type and delayed graft function no effect on rejection, so values "
        "near zero there are the expected result, not a failure.",
        "",
        *table(["Feature", "Logistic regression", "LightGBM"], rows),
    ]


# --- 8. sensitivity analyses ------------------------------------------------------------


def row_for(name: str, dev: Development, label: str | None = None) -> list[str]:
    label = label or dev.label
    kind = dev.champion
    cells = [name, NAMES[kind]]
    for part in (dev.internal, dev.external):
        part = part[part[label].notna()]
        y = part[label].to_numpy(dtype=float)
        p = part[score_column(kind)].to_numpy()
        rate = float(part["rule_alert"].mean())
        same = p >= threshold_for_rate(p, rate)
        cells += [
            f"{auroc(y, p):.3f}",
            f"{auprc(y, p):.3f}",
            str(int(y.sum())),
            f"{pct(float(same[y == 1].mean()))} vs {pct(float(part['rule_alert'][y == 1].mean()))}",
        ]
    return cells


def sensitivity_section(
    loaded: Loaded, truth: Truth, main: Development, as_of: date
) -> tuple[Lines, dict[str, Any]]:
    rows = [row_for("Main analysis", main)]
    border = develop(loaded.frame, label="label_borderline")
    rows.append(row_for("Borderline counted as rejection", border))
    gt = Development(
        main.label,
        main.cv,
        main.best,
        main.champion,
        main.development,
        with_truth(main.internal, loaded, truth, as_of),
        with_truth(main.external, loaded, truth, as_of),
        main.models,
    )
    rows.append(row_for("Test labels from the truth (same models)", gt, label="truth_label"))
    no_pre = develop(loaded.frame[~loaded.frame["pre_biopsy"]].reset_index(drop=True))
    rows.append(row_for("(a) Without draws <= 14 days before a biopsy", no_pre))
    by_reason = develop(truth_reason_visits(loaded, truth, as_of))
    rows.append(row_for("(b) Visits = truly scheduled draws", by_reason))
    organ_rows = []
    tests = pd.concat([main.internal, main.external], ignore_index=True)
    for organ, part in tests.groupby("organ", sort=True):
        y = part[main.label].to_numpy(dtype=float)
        p = part[score_column(main.champion)].to_numpy()
        events = int(y.sum())
        scored = events >= MIN_EVENTS
        organ_rows.append(
            [
                str(organ),
                str(len(part)),
                count(events),
                f"{auroc(y, p):.3f}" if scored else "not scored",
                f"{auprc(y, p):.3f}" if scored else "not scored",
            ]
        )
    lines = [
        "Each line reruns the whole development (selection, refits, tests) unless noted. "
        "Cells: AUROC, AUPRC, rejection visits, and rejection visits caught by the model vs the "
        "rule engine at the same alert burden.",
        "",
        *table(
            [
                "Analysis",
                "Champion",
                "Int. AUROC",
                "Int. AUPRC",
                "Int. events",
                "Int. caught",
                "Ext. AUROC",
                "Ext. AUPRC",
                "Ext. events",
                "Ext. caught",
            ],
            rows,
        ),
        "",
        "(a) uses the future (it knows a biopsy is coming) and could never run live; it shows "
        "how much of the result rests on draws taken because a clinician was already worried. "
        "(b) uses the generator's draw reason, which OMOP does not have.",
        "",
        "**By organ** (champion, internal + external test visits):",
        "",
        *table(["Organ", "Visits", "Rejection visits", "AUROC", "AUPRC"], organ_rows),
    ]
    head = {
        "truth_labels_external_auroc": auroc(*xy(gt.external, "truth_label", main.champion)),
        "no_pre_biopsy_external_auroc": auroc(*xy(no_pre.external, "label", no_pre.champion)),
    }
    return lines, head


def xy(part: pd.DataFrame, label: str, kind: Kind) -> tuple[np.ndarray, np.ndarray]:
    part = part[part[label].notna()]
    return part[label].to_numpy(dtype=float), part[score_column(kind)].to_numpy()


def with_truth(part: pd.DataFrame, loaded: Loaded, truth: Truth, as_of: date) -> pd.DataFrame:
    return part.assign(
        truth_label=[
            truth.label(loaded.patient_of[k], v, as_of)
            for k, v in zip(part["key"], part["visit"], strict=True)
        ]
    )


def truth_reason_visits(loaded: Loaded, truth: Truth, as_of: date) -> pd.DataFrame:
    """Visits at the generator's scheduled dd-cfDNA draws instead of the schedule rule."""
    rows = []
    for record, config in loaded.records:
        days = sorted(set(truth.surveillance_draws.get(loaded.patient_of[record.key], [])))
        days = [d for d in days if d <= as_of and (record.end_day is None or d < record.end_day)]
        rows += visit_rows(record, config, as_of, visit_days=days)
    return pd.DataFrame(rows, columns=COLUMNS)


# --- 9. fairness ------------------------------------------------------------------------


def fairness_section(summary: Summary) -> Lines:
    body = []
    for r in summary.fairness:
        scored = r["auroc"] is not None
        body.append(
            [
                {"age_band": "Age band", "sex": "Sex"}[r["by"]],
                r["group"],
                str(r["n_visits"]),
                count(r["n_events"]),
                f"{r['auroc']:.3f}" if scored else "not scored",
                f"{r['brier']:.4f}" if scored else "",
                pct(r["alert_rate"]),
                pct(r["sensitivity"]) if scored else "",
            ]
        )
    return [
        f"Champion on all test visits (internal + external), alerting at the rule engine's "
        f"rate. Groups with fewer than {MIN_EVENTS} rejection visits are not scored. The "
        "generator gives age and sex no effect on rejection, so differences here are noise "
        "or follow from other features.",
        "",
        *table(
            [
                "By",
                "Group",
                "Visits",
                "Rejection visits",
                "AUROC",
                "Brier",
                "Alert rate",
                "Rejections caught",
            ],
            body,
        ),
    ]


# --- 10. drift --------------------------------------------------------------------------


def drift_section(
    dev: Development, frame: pd.DataFrame, engine: Engine
) -> tuple[Lines, dict[str, Any]]:
    ab = frame[frame["tenant"].isin(TRAIN_CENTERS)]
    c = frame[frame["tenant"] == EXTERNAL_CENTER]
    early = frame[frame["transplant_year"] <= 2023]
    late = frame[frame["transplant_year"] >= 2024]
    body = []
    worst = 0.0
    for name in FEATURE_NAMES:
        between = psi(ab[name].to_numpy(dtype=float), c[name].to_numpy(dtype=float))
        over_time = psi(early[name].to_numpy(dtype=float), late[name].to_numpy(dtype=float))
        worst = max(worst, between)
        body.append(
            [
                LABELS[name],
                f"{between:.3f}",
                drift_band(between),
                f"{over_time:.3f}",
                drift_band(over_time),
            ]
        )
    model = dev.models[f"external_{dev.champion.value}"]
    scores = {
        name: model.predict_proba(part[list(FEATURE_NAMES)])[:, 1]
        for name, part in (("ab", ab), ("c", c), ("early", early), ("late", late))
    }
    score_ab_c = psi(scores["ab"], scores["c"])
    score_time = psi(scores["early"], scores["late"])
    body.append(
        [
            "**Risk score**",
            f"{score_ab_c:.3f}",
            drift_band(score_ab_c),
            f"{score_time:.3f}",
            drift_band(score_time),
        ]
    )
    lines = [
        "Population stability index (PSI): < 0.1 stable, 0.1-0.25 watch, > 0.25 shifted. "
        "Missing values form their own bin. Over time compares visits of patients transplanted "
        "in 2022-2023 with 2024-2025 (the later group has shorter follow-up, so time since "
        "transplant shifts by design).",
        "",
        *table(["Feature", "A+B vs C", "", "Early vs late transplants", ""], body),
        "",
        *nightly_lines(engine),
    ]
    return lines, {"max_feature_psi_ab_vs_c": worst, "score_psi_ab_vs_c": score_ab_c}


def nightly_lines(engine: Engine) -> Lines:
    """Score PSI that nightly scoring stored against the training reference (if any)."""
    rows = []
    for tenant in center_tenants():
        with tenant_transaction(engine, tenant) as conn:
            exists = conn.execute(text("SELECT to_regclass('app.risk_run')")).scalar()
            if not exists:
                return ["Nightly scoring has not been set up in this database yet."]
            found = conn.execute(
                text(
                    "SELECT as_of, model_version, scored, score_psi, feature_psi "
                    "FROM app.risk_run ORDER BY created_at DESC LIMIT 1"
                )
            ).one_or_none()
        if found is not None:
            rows.append(
                [
                    tenant,
                    found.as_of.isoformat(),
                    f"v{found.model_version}",
                    str(found.scored),
                    "n/a" if found.score_psi is None else f"{found.score_psi:.3f}",
                    "n/a" if found.score_psi is None else drift_band(found.score_psi),
                    worst_feature(found.feature_psi),
                ]
            )
    if not rows:
        return ["No nightly scoring run is stored yet (`make surveillance-run`)."]
    return [
        "**Latest nightly scoring run per center.** The nightly job scores each active "
        "patient's latest visit and compares the scores and inputs with the same population "
        "at training time (the reference stored with the model version):",
        "",
        *table(
            ["Center", "As of", "Model", "Patients scored", "Score PSI", "", "Largest feature PSI"],
            rows,
        ),
    ]


def worst_feature(values: dict[str, float]) -> str:
    """The feature that drifted most in a nightly run."""
    if not values:
        return "n/a"
    name = max(values, key=lambda k: values[k])
    return f"{LABELS.get(name, name)} {values[name]:.3f} ({drift_band(values[name])})"


# --- 11. registry -----------------------------------------------------------------------


def registry_section(summary: Summary) -> tuple[Lines, dict[str, Any]]:
    import mlflow
    from mlflow import MlflowClient

    from txlens_backend.risk.registry import ALIAS, MODEL_NAME, champion_version

    uri = get_settings().mlflow_tracking_uri
    version = champion_version(uri)
    if version is None:
        return ["No model is registered yet (`make risk-train`)."], {"version": None}
    mlflow.set_tracking_uri(uri)
    client = MlflowClient()
    mv = client.get_model_version(MODEL_NAME, version)
    logged = client.get_run(str(mv.run_id)).data.metrics
    key = f"external_{summary.champion.value}_auroc"
    here = summary.external[summary.champion].auroc.value
    same = key in logged and abs(logged[key] - here) < 1e-9
    return [
        f"`{MODEL_NAME}@{ALIAS}` is version {version} (run `{mv.run_id}`). Its logged external "
        f"AUROC is {logged.get(key, float('nan')):.4f}; this report's is {here:.4f}: "
        + (
            "the same development, as expected."
            if same
            else "**different**: the registered version was trained on other data or code. Run "
            "`make risk-train` to register a version that matches this report."
        ),
    ], {"version": version, "matches_report": same}


# --- report -----------------------------------------------------------------------------


def render(sections: list[tuple[str, Lines]], as_of: date) -> str:
    lines = [
        "# Rejection-risk model",
        "",
        f"**{LABEL_TEXT}**",
        "",
        f"Generated {date.today().isoformat()} by `make risk-report` "
        f"(evals/src/txlens_evals/risk_model.py) on data extracted {as_of.isoformat()}. "
        "Synthetic data only. Task: at each scheduled surveillance visit, once its results are "
        "back, predict a biopsy showing TCMR, ABMR or mixed rejection in the next 90 days. "
        "Model card: docs/model_card_risk.md.",
    ]
    for n, (title, body) in enumerate(sections, start=1):
        lines += ["", f"## {n}. {title}", "", *body]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--truth", type=Path, default=Path("data/ground_truth"))
    parser.add_argument("--out", type=Path, default=Path("evals/reports/risk_model.md"))
    args = parser.parse_args(argv)
    engine = make_engine(get_settings().app_database_url.get_secret_value())
    check_role_is_rls_bound(engine)
    truth = Truth.read(args.truth)
    loaded = load(engine, args.as_of, args.truth)
    dev = develop(loaded.frame)
    summary = summarize(dev)
    figures = args.out.parent / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    calibration_figure(dev, figures / "risk_calibration.png")
    data_lines, data_head = data_section(loaded, truth, args.as_of)
    visit_lines, visit_head = visits_section(loaded, truth)
    burden_lines, burden_head = burden_section(dev, summary)
    sens_lines, sens_head = sensitivity_section(loaded, truth, dev, args.as_of)
    drift_lines, drift_head = drift_section(dev, loaded.frame, engine)
    registry_lines, registry_head = registry_section(summary)
    engine.dispose()
    sections = [
        ("Data and labels", data_lines),
        ("Which draws became visits", visit_lines),
        ("Model choice (cross-validation)", selection_section(dev)),
        ("Discrimination and calibration", scores_section(summary)),
        ("Against the rule engine at the same alert burden", burden_lines),
        ("Decision curves", dca_section(dev, figures / "risk_dca.png")),
        ("What the models use", importance_section(dev)),
        ("Sensitivity analyses", sens_lines),
        ("Fairness: age band and sex", fairness_section(summary)),
        ("Drift", drift_lines),
        ("Registered model", registry_lines),
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(sections, args.as_of), encoding="utf-8")
    champion = summary.champion
    write_headline(
        args.out.with_suffix(".json"),
        {
            "champion": champion.value,
            "internal": summary.internal[champion].as_dict(),
            "external": summary.external[champion].as_dict(),
            "burden": burden_head,
            "data": data_head,
            "visits": visit_head,
            "sensitivity": sens_head,
            "drift": drift_head,
            "registry": registry_head,
        },
    )
    print(args.out.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
