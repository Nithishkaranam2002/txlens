"""Survival analysis evaluation: `make survival-report` -> evals/reports/survival.md (+ .json).

On the pipeline-loaded database (every center, read center by center as the app login) and on
the simulated extract with known hazard ratios (backend survival/simulate.py):

1. R vs Python parity on both extracts (needs the R image, `make r-build`; skipped without it).
2. Hazard ratios recovered on the simulated extract against the truth.
3. Immortal time bias: naive vs landmark vs time-varying, as a Monte Carlo on simulated data
   (true HR 1) and once on the main data.
4. Graft survival by biopsy-proven rejection in year 1, from a 1-year landmark (main data).
5. Cumulative incidence (Aalen-Johansen) vs 1 - Kaplan-Meier.
6. The main data's Cox model (hazard ratios near 1, and why).
7. Delayed graft function read by the note NLP vs the generator's flag.
8. How much DGF misclassification would move the DGF hazard ratio.

Synthetic data only.
"""

import argparse
import csv
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from lifelines import CoxPHFitter
from scipy.stats import beta
from sqlalchemy import Engine, text

from txlens_backend.config import get_settings
from txlens_backend.db import check_role_is_rls_bound, make_engine, tenant_transaction
from txlens_backend.db_setup import center_tenants
from txlens_backend.deid import suppress
from txlens_backend.survival.competing import aalen_johansen
from txlens_backend.survival.cox import COVARIATES, FIT_OPTIONS, CoxResult, fit_cox
from txlens_backend.survival.extract import DEATH, GRAFT_LOSS, extract
from txlens_backend.survival.km import Z95, Curve, kaplan_meier, log_rank, value_at
from txlens_backend.survival.landmark import (
    GROUPINGS,
    YEAR,
    HazardRatio,
    at_landmark,
    immortal_time_comparison,
)
from txlens_backend.survival.simulate import TRUE_HAZARD_RATIOS, misclassify, simulate
from txlens_evals.stats import write_headline
from txlens_evals.survival_parity import TOLERANCES, Parity, parity, r_available

THRESHOLD = 11
REPLICATES = 50  # Monte Carlo datasets for the immortal-time demonstration
LARGE_N = 20_000  # simulated patients for the misclassification scenarios (less noise)
DSA_LANDMARK = 30
HORIZONS = (365, 730, 1095)


# --- data -------------------------------------------------------------------------------


def main_extract(engine: Engine) -> pd.DataFrame:
    """Every center's extract with the patient's MRN (to join the ground truth), RLS on."""
    frames = []
    for tenant in center_tenants():
        with tenant_transaction(engine, tenant) as conn:
            frame = extract(conn)
            rows = conn.execute(text("SELECT person_id, mrn FROM app.patient_identity")).all()
            mrn: dict[int, str] = {int(p): str(m) for p, m in rows}
        frames.append(frame.assign(tenant=tenant, mrn=frame["person_id"].map(mrn)))
    return pd.concat(frames, ignore_index=True)


def true_dgf(frame: pd.DataFrame, truth: Path) -> pd.Series:
    """The generator's DGF flag for each row (NaN for heart grafts, as in the extract)."""
    with (truth / "patients.csv").open() as f:
        flag = {(p["tenant_id"], p["mrn"]): float(p["dgf"] == "true") for p in csv.DictReader(f)}
    values = [flag[(t, m)] for t, m in zip(frame["tenant"], frame["mrn"], strict=True)]
    return pd.Series(np.where(frame["organ"] == "heart", np.nan, values), index=frame.index)


# --- small helpers ----------------------------------------------------------------------


def hr_text(hr: float, low: float, high: float) -> str:
    return f"{hr:.2f} ({low:.2f}-{high:.2f})"


def count(n: int) -> str:
    """A count as the app would show it."""
    return str(suppress(n, THRESHOLD))


def cox_terms(result: CoxResult) -> dict[str, tuple[float, float, float]]:
    """covariate -> (HR, low, high)."""
    return {t.key: (t.hazard_ratio, t.lower, t.upper) for t in result.terms}


# --- 1. parity --------------------------------------------------------------------------


def parity_section(main: pd.DataFrame, sim: pd.DataFrame) -> tuple[list[str], dict[str, Any]]:
    """R vs Python on the simulated and the OMOP extract."""
    if not r_available():
        return (
            [
                "Skipped: the R image is not built (`make r-build`). CI's `r-parity` job "
                "checks the same tolerances on every push."
            ],
            {"ran": False},
        )
    with tempfile.TemporaryDirectory() as folder:
        results = {
            "simulated": parity(sim, sim["donor_type"], Path(folder) / "sim"),
            "OMOP": parity(main, main["organ"], Path(folder) / "omop"),
        }
    sim_r = results["simulated"].r
    lines = [
        f"R {sim_r['r_version'].removeprefix('R version ')}, survival {sim_r['survival_version']}, "
        f"cmprsk {sim_r['cmprsk_version']} (Docker image `txlens-r:local`) vs the Python port. "
        f"Simulated: {len(sim):,} patients. OMOP: the {len(main):,} pipeline-loaded patients.",
        "",
        "| Quantity | Values compared (sim / OMOP) | Max abs. difference, simulated | "
        "Max abs. difference, OMOP | Tolerance | Within |",
        "|---|---|---|---|---|---|",
    ]
    by_name = {k: {c.quantity: c for c in v.comparisons} for k, v in results.items()}
    for quantity in TOLERANCES:
        s, o = by_name["simulated"][quantity], by_name["OMOP"][quantity]
        lines.append(
            f"| {quantity} | {s.values:,} / {o.values:,} | {s.max_abs_diff:.1e} | "
            f"{o.max_abs_diff:.1e} | {s.tolerance} | "
            f"{'yes' if s.passed and o.passed else '**NO**'} |"
        )
    lines += ["", *ph_lines(results)]
    passed = all(c.passed for r in results.values() for c in r.comparisons)
    worst_cox = max(by_name[k]["cox_coef"].max_abs_diff for k in by_name)
    return lines, {"ran": True, "all_within_tolerance": passed, "max_cox_coef_diff": worst_cox}


def ph_lines(results: dict[str, Parity]) -> list[str]:
    """Schoenfeld p-values (reported, not asserted) and Gray's test."""
    checks = [(k, c) for k, r in results.items() for c in r.ph]
    same = sum(c.same_verdict for _, c in checks)
    lines = [
        f"**Proportional-hazards tests** (reported, not asserted: R's `cox.zph` uses a score test "
        f"since survival 3.0, lifelines the older approximation). Same verdict at p < 0.05 for "
        f"{same} of {len(checks)} covariate tests.",
        "",
        "| Extract | Model | Covariate | R cox.zph p | lifelines p |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {k} | {c.model} | {c.covariate} | {c.r_p:.3f} | {c.python_p:.3f} |" for k, c in checks
    ]
    gray = results["simulated"].r["gray_test"]
    lines += [
        "",
        f"Gray's test (R only, simulated, donor type): graft loss p = {gray['p'][0]:.2g}, "
        f"death p = {gray['p'][1]:.2g}. Library differences: [r_parity/README.md]"
        "(../../r_parity/README.md).",
    ]
    return lines


# --- 2. recovered hazard ratios ---------------------------------------------------------


def recovery_section(sim: pd.DataFrame) -> list[str]:
    """Estimated vs true hazard ratios on the simulated extract."""
    kidney = fit_cox(at_landmark(sim, DSA_LANDMARK), list(COVARIATES))
    everyone = fit_cox(sim, ["age_per_decade", "deceased_donor", "heart"])
    rows = [("kidney model, from day 30", key, est) for key, est in cox_terms(kidney).items()]
    rows += [("all organs", "heart", cox_terms(everyone)["heart"])]
    lines = [
        f"Simulated extract ({len(sim):,} patients). The kidney model starts at the month-1 DSA "
        f"test ({kidney.n:,} patients, {kidney.events} graft losses); heart grafts have no DSA "
        "or DGF, so the heart effect comes from a second model on every patient.",
        "",
        "| Model | Covariate | True HR | Estimated HR (95% CI) | Truth inside CI |",
        "|---|---|---|---|---|",
    ]
    for model, key, (hr, low, high) in rows:
        truth = TRUE_HAZARD_RATIOS[key]
        lines.append(
            f"| {model} | {COVARIATES[key]} | {truth} | {hr_text(hr, low, high)} | "
            f"{'yes' if low <= truth <= high else 'no'} |"
        )
    return lines


# --- 3. immortal time -------------------------------------------------------------------


@dataclass(frozen=True)
class MonteCarlo:
    """Per design: geometric-mean HR and how often its 95% CI excluded 1 (true HR is 1)."""

    design: str
    mean_hr: float
    excluded_one: float


def monte_carlo(replicates: int) -> list[MonteCarlo]:
    """The three designs on `replicates` simulated datasets (seeds 1..replicates)."""
    runs = [
        immortal_time_comparison(simulate(seed=s), "dd_cfdna_high_day")
        for s in range(1, replicates + 1)
    ]
    out = []
    for i, design in enumerate(r.design for r in runs[0]):
        hrs = np.array([run[i].hazard_ratio for run in runs])
        excluded = np.mean([not run[i].lower <= 1 <= run[i].upper for run in runs])
        out.append(MonteCarlo(design, float(np.exp(np.log(hrs).mean())), float(excluded)))
    return out


def main_verdict(observed: list[HazardRatio]) -> str:
    """What the three designs say on the main data (from the numbers, not assumed)."""
    naive, landmark, varying = observed
    if naive.hazard_ratio < min(landmark.hazard_ratio, varying.hazard_ratio):
        return ("The naive design gives the smallest hazard ratio: the exposed group's early, "
                "immortal follow-up dilutes a real harm. The landmark and time-varying designs "
                "both give a larger effect.")  # fmt: skip
    return ("Here the naive design does not give the smallest hazard ratio; the bias is shown "
            "by the simulated datasets above, where the truth is known.")  # fmt: skip


def immortal_section(main: pd.DataFrame) -> tuple[list[str], dict[str, Any]]:
    """Monte Carlo on simulated data (true HR 1), then the main data."""
    mc = monte_carlo(REPLICATES)
    observed = immortal_time_comparison(main, "dd_cfdna_high_day")
    lines = [
        "Exposure: **dd-cfDNA >= 1% at any test in year 1**. A patient can only have that result "
        'while still followed with a working graft, so comparing "ever high in year 1" with '
        '"never" from transplant gives the exposed group event-free time for free.',
        "",
        f"**Simulated, true HR = 1** ({REPLICATES} datasets of 3,000 patients; the test result is "
        "random and unrelated to the outcome):",
        "",
        "| Design | Mean HR (geometric) | 95% CI excluded 1 in | Verdict |",
        "|---|---|---|---|",
    ]
    for m in mc:
        verdict = "biased (looks protective)" if m.mean_hr < 0.85 else "unbiased"
        lines.append(
            f"| {m.design} | {m.mean_hr:.2f} | {m.excluded_one:.0%} of datasets | {verdict} |"
        )
    lines += [
        "",
        "With no true effect, a correct design should exclude 1 in about 5% of datasets.",
        "",
        f"**Main data** ({len(main):,} patients). Here high dd-cfDNA marks rejection, which the "
        "generator links to later graft loss, so there is a real effect:",
        "",
        "| Design | HR (95% CI) | Patients | Graft losses |",
        "|---|---|---|---|",
        *(
            f"| {h.design} | {hr_text(h.hazard_ratio, h.lower, h.upper)} | {h.n:,} | "
            f"{count(h.events)} |"
            for h in observed
        ),
        "",
        main_verdict(observed),
    ]
    headline = {m.design: round(m.mean_hr, 4) for m in mc}
    return lines, {
        "simulated_mean_hr": headline,
        "main": {h.design: round(h.hazard_ratio, 3) for h in observed},
    }


# --- 4. rejection in year 1 -------------------------------------------------------------


def rejection_section(main: pd.DataFrame) -> tuple[list[str], dict[str, Any]]:
    """Graft survival from day 365 by biopsy-proven rejection in year 1."""
    data = at_landmark(main, YEAR)
    groups = GROUPINGS["rejection_year1"].assign(main).loc[data.index]
    loss = (data["status"] == GRAFT_LOSS).astype(int)
    test = log_rank(data["time"], loss, groups)
    design = pd.DataFrame(
        {
            "time": data["time"].astype(float),
            "event": loss,
            "abmr": (groups == "ABMR in year 1").astype(float),
            "other": (groups == "Other rejection in year 1").astype(float),
        }
    )
    fit = CoxPHFitter().fit(design, "time", "event", fit_options=FIT_OPTIONS)
    hr = {
        k: (
            float(np.exp(fit.params_[k])),
            float(np.exp(fit.params_[k] - Z95 * fit.standard_errors_[k])),
            float(np.exp(fit.params_[k] + Z95 * fit.standard_errors_[k])),
        )
        for k in ("abmr", "other")
    }
    lines = [
        f"Landmark at day 365: {len(data):,} patients still with a working graft and followed. "
        "Groups use only biopsies by day 365.",
        "",
        "| Group | Patients | Graft losses after day 365 | 2-year graft survival from the "
        "landmark (KM) | HR vs no rejection (95% CI) |",
        "|---|---|---|---|---|",
    ]
    for group in sorted(groups.unique()):
        part = groups == group
        km = kaplan_meier(data.loc[part, "time"], loss[part], group)
        key = "abmr" if group.startswith("ABMR") else "other" if group.startswith("Other") else None
        lines.append(
            f"| {group} | {count(int(part.sum()))} | {count(int(loss[part].sum()))} | "
            f"{value_at(km, km.estimate, 730):.1%} | "
            f"{hr_text(*hr[key]) if key else 'reference'} |"
        )
    lines += [
        "",
        f"Log-rank chi-square {test.chi2:.1f} on {test.df} df, p = {test.p_value:.1e}. "
        "This is the effect the generator does build in: an ABMR episode that does not "
        "resolve leads to graft loss 2 to 18 months after the biopsy.",
    ]
    return lines, {"abmr_hr": [round(v, 2) for v in hr["abmr"]], "logrank_p": float(test.p_value)}


# --- 5. competing risks -----------------------------------------------------------------


def incidence_rows(label: str, frame: pd.DataFrame) -> tuple[list[str], float]:
    """CIF vs 1 - KM at 1-3 years for one extract; returns rows and the 3-year gap."""
    cif = aalen_johansen(frame["time"], frame["status"], GRAFT_LOSS)
    km: Curve = kaplan_meier(frame["time"], (frame["status"] == GRAFT_LOSS).astype(int))
    rows, gap = [], 0.0
    for day in HORIZONS:
        a, b = value_at(cif, cif.estimate, day, 0.0), 1 - value_at(km, km.estimate, day)
        gap = b - a
        rows.append(
            f"| {label} | {day // 365} | {a:.2%} | {b:.2%} | {gap * 100:+.2f} pts "
            f"({gap / a:+.0%}) |"
            if a
            else f"| {label} | {day // 365} | 0 | {b:.2%} | |"
        )
    return rows, gap


def competing_section(main: pd.DataFrame, sim: pd.DataFrame) -> tuple[list[str], dict[str, Any]]:
    """Why 1 - KM overstates graft loss when death competes."""
    main_rows, main_gap = incidence_rows("main data", main)
    sim_rows, sim_gap = incidence_rows("simulated", sim)
    deaths = int((main["status"] == DEATH).sum())
    lines = [
        "1 - Kaplan-Meier treats a patient who died with a working graft as censored: as if "
        "they were still at risk of graft loss later. They are not, so 1 - KM estimates graft "
        "loss in a world without death and overstates the real risk. Aalen-Johansen adds "
        "graft-loss risk only while the patient is alive with a working graft.",
        "",
        "| Extract | Year | Cumulative incidence (Aalen-Johansen) | 1 - KM "
        "| 1 - KM overstates by |",
        "|---|---|---|---|---|",
        *main_rows,
        *sim_rows,
        "",
        f"The main data has {deaths} deaths with a functioning graft against "
        f"{int((main['status'] == GRAFT_LOSS).sum())} graft losses, mostly late, so the gap is "
        "small; it grows with follow-up and with the death rate (the simulated cohort has "
        "more deaths).",
    ]
    return lines, {
        "gap_3y_main_pts": round(main_gap * 100, 2),
        "gap_3y_sim_pts": round(sim_gap * 100, 2),
    }


# --- 6. main Cox ------------------------------------------------------------------------


def main_cox_section(main: pd.DataFrame) -> list[str]:
    """The main data's model: hazard ratios near 1, explained."""
    kidney = fit_cox(at_landmark(main, DSA_LANDMARK), list(COVARIATES))
    everyone = fit_cox(main, ["age_per_decade", "deceased_donor", "heart"])
    lines = [
        "| Model | Covariate | HR (95% CI) | PH test p |",
        "|---|---|---|---|",
    ]
    for name, result in (("kidney, from day 30", kidney), ("all organs", everyone)):
        lines += [
            f"| {name} ({result.n:,} patients, {count(result.events)} graft losses) | "
            f"{t.label} | {hr_text(t.hazard_ratio, t.lower, t.upper)} | {t.ph_p_value:.2f} |"
            for t in result.terms
        ]
    terms = kidney.terms + everyone.terms
    null = sorted({t.label for t in terms if t.lower <= 1 <= t.upper})
    real = sorted({t.label for t in terms if not t.lower <= 1 <= t.upper})
    lines += [
        "",
        f"Intervals that include 1: {'; '.join(null) or 'none'}. That is the right answer for "
        "this data: the generator draws graft loss and death as flat probabilities that do not "
        "depend on age, donor type, organ or DGF.",
        "",
        f"Intervals that exclude 1: {'; '.join(real) or 'none'}. The generator gives DSA-positive "
        "patients more antibody-mediated rejection, and unresolved rejection leads to graft "
        "loss, so a positive month-1 DSA test does predict graft loss here (through rejection, "
        "not as a direct effect).",
        "",
        "The method recovers real effects on the simulated extract (section 2).",
    ]
    return lines


# --- 7. DGF from notes ------------------------------------------------------------------


def clopper_pearson(x: int, n: int) -> tuple[float, float]:
    """Exact 95% interval for x successes in n (bootstrap collapses at 100%)."""
    low = float(beta.ppf(0.025, x, n - x + 1)) if x > 0 else 0.0
    high = float(beta.ppf(0.975, x + 1, n - x)) if x < n else 1.0
    return low, high


@dataclass(frozen=True)
class Accuracy:
    tp: int
    fp: int
    fn: int
    tn: int

    @property
    def sensitivity(self) -> float:
        return self.tp / (self.tp + self.fn)

    @property
    def specificity(self) -> float:
        return self.tn / (self.tn + self.fp)

    @property
    def ppv(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0


def dgf_accuracy(main: pd.DataFrame, truth: pd.Series) -> Accuracy:
    """NLP DGF vs the generator's flag, kidney grafts."""
    kidney = main["organ"] == "kidney"
    read, real = main.loc[kidney, "dgf"] == 1, truth[kidney] == 1
    return Accuracy(
        int((read & real).sum()),
        int((read & ~real).sum()),
        int((~read & real).sum()),
        int((~read & ~real).sum()),
    )


def dgf_section(acc: Accuracy) -> tuple[list[str], dict[str, Any]]:
    sens_ci = clopper_pearson(acc.tp, acc.tp + acc.fn)
    spec_ci = clopper_pearson(acc.tn, acc.tn + acc.fp)
    ppv_ci = clopper_pearson(acc.tp, acc.tp + acc.fp)
    lines = [
        'DGF reaches OMOP as a "History of event" observation (concept 1340204) valued with '
        "SNOMED 426136000, from affirmed past mentions in clinic notes (ADR 0017). Kidney "
        "grafts, per patient:",
        "",
        "| Metric | Value (exact 95% CI) | Counts |",
        "|---|---|---|",
        f"| Sensitivity | {acc.sensitivity:.1%} ({sens_ci[0]:.1%}-{sens_ci[1]:.1%}) | "
        f"{acc.tp} of {acc.tp + acc.fn} |",
        f"| Specificity | {acc.specificity:.1%} ({spec_ci[0]:.1%}-{spec_ci[1]:.1%}) | "
        f"{acc.tn} of {acc.tn + acc.fp} |",
        f"| Positive predictive value | {acc.ppv:.1%} ({ppv_ci[0]:.1%}-{ppv_ci[1]:.1%}) | "
        f"{acc.tp} of {acc.tp + acc.fp} |",
        "",
        "Template notes make this easy: real notes would not be read perfectly.",
    ]
    return lines, {
        "sensitivity": acc.sensitivity,
        "specificity": acc.specificity,
        "sensitivity_low": sens_ci[0],
        "specificity_low": spec_ci[0],
    }


# --- 8. misclassification ---------------------------------------------------------------


def dgf_hr(frame: pd.DataFrame) -> tuple[float, float, float]:
    """The DGF hazard ratio of the full kidney model from day 30."""
    return cox_terms(fit_cox(at_landmark(frame, DSA_LANDMARK), list(COVARIATES)))["dgf"]


def sensitivity_section(
    main: pd.DataFrame, truth: pd.Series, accuracy: dict[str, Any]
) -> list[str]:
    """Main data: NLP vs true DGF. Simulated: true DGF (HR 1.8) read with error."""
    sim = simulate(n=LARGE_N)
    reference = dgf_hr(sim)[0]
    nlp = cox_terms(fit_cox(at_landmark(main, DSA_LANDMARK), list(COVARIATES)))
    true = cox_terms(fit_cox(at_landmark(main.assign(dgf=truth), DSA_LANDMARK), list(COVARIATES)))
    lines = [
        "**Main data**: the kidney Cox model with DGF from the NLP vs the generator's true flag.",
        "",
        "| Covariate | HR with NLP DGF | HR with true DGF | Change |",
        "|---|---|---|---|",
        *(
            f"| {COVARIATES[k]} | {hr_text(*nlp[k])} | {hr_text(*true[k])} | "
            f"{nlp[k][0] / true[k][0] - 1:+.1%} |"
            for k in nlp
        ),
        "",
        f"**Simulated** ({LARGE_N:,} patients, true DGF HR = {TRUE_HAZARD_RATIOS['dgf']}): the "
        "same DGF read with errors. Non-differential misclassification pulls the hazard ratio "
        f"toward 1. With the true flag this sample estimates {reference:.2f} (sampling noise "
        "around 1.8); each change below is against that estimate.",
        "",
        "| DGF read with | Sensitivity | Specificity | DGF HR (95% CI) | Change vs true DGF |",
        "|---|---|---|---|---|",
    ]

    scenarios = [
        ("no error (true flag)", 1.0, 1.0),
        ("the NLP's measured rates", accuracy["sensitivity"], accuracy["specificity"]),
        (
            "the NLP's worst plausible rates (95% CI lower bounds)",
            accuracy["sensitivity_low"],
            accuracy["specificity_low"],
        ),
        ("a weaker reader", 0.90, 0.95),
        ("a poor reader", 0.75, 0.90),
    ]
    for name, sens, spec in scenarios:
        hr = dgf_hr(misclassify(sim, "dgf", sens, spec))
        lines.append(
            f"| {name} | {sens:.1%} | {spec:.1%} | {hr_text(*hr)} | {hr[0] / reference - 1:+.1%} |"
        )
    lines += [
        "",
        "With the NLP as accurate as measured here, DGF misclassification does not "
        "move the statistics; a reader at 75% sensitivity would understate the DGF "
        "effect noticeably.",
    ]
    return lines


# --- report -----------------------------------------------------------------------------


def render(sections: list[tuple[str, list[str]]]) -> str:
    lines = [
        "# Survival analysis: R parity, immortal time, competing risks",
        "",
        f"Generated {date.today().isoformat()} by `make survival-report` "
        "(evals/src/txlens_evals/survival.py). Synthetic data only. Counts of 1-10 are shown "
        'as "<11", as in the app.',
    ]
    for n, (title, body) in enumerate(sections, start=1):
        lines += ["", f"## {n}. {title}", "", *body]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Write the report and its headline JSON; exit 1 if R parity ran and failed."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--truth", type=Path, default=Path("data/ground_truth"))
    parser.add_argument("--out", type=Path, default=Path("evals/reports/survival.md"))
    args = parser.parse_args(argv)
    engine = make_engine(get_settings().app_database_url.get_secret_value())
    check_role_is_rls_bound(engine)
    main_data = main_extract(engine)
    engine.dispose()
    sim = simulate()
    truth = true_dgf(main_data, args.truth)
    parity_lines, parity_head = parity_section(main_data, sim)
    immortal_lines, immortal_head = immortal_section(main_data)
    rejection_lines, rejection_head = rejection_section(main_data)
    competing_lines, competing_head = competing_section(main_data, sim)
    dgf_lines, dgf_head = dgf_section(dgf_accuracy(main_data, truth))
    sections = [
        ("R vs Python parity", parity_lines),
        ("Known hazard ratios recovered (simulated extract)", recovery_section(sim)),
        ("Immortal time bias: naive vs landmark vs time-varying", immortal_lines),
        ("Graft survival by rejection in year 1 (landmark at 1 year)", rejection_lines),
        ("Competing risks: cumulative incidence vs 1 - Kaplan-Meier", competing_lines),
        ("Cox model on the main data", main_cox_section(main_data)),
        ("Delayed graft function from clinic notes", dgf_lines),
        (
            "DGF misclassification: sensitivity analysis",
            sensitivity_section(main_data, truth, dgf_head),
        ),
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(sections), encoding="utf-8")
    write_headline(
        args.out.with_suffix(".json"),
        {
            "patients": len(main_data),
            "parity": parity_head,
            "immortal_time": immortal_head,
            "rejection_year1": rejection_head,
            "competing_risks": competing_head,
            "dgf_nlp": dgf_head,
        },
    )
    print(args.out.read_text(encoding="utf-8"))
    return 1 if parity_head["ran"] and not parity_head["all_within_tolerance"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
