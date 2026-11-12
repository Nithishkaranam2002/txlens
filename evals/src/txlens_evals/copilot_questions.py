"""The copilot evaluation's questions and their known answers.

- 40 data questions about center A, each with: the tool a good plan would use, how to compute
  the answer from the generator's ground truth, and a reference query on the database. The
  reference must agree with the truth first; where it does not (a pipeline gap, not the
  copilot's), the question is reported and left out of accuracy.
- 20 literature questions the open-access corpus can answer, and 3 it cannot.
- Ground truth uses years only for ages (OMOP keeps the birth year) and leaves out the lab
  results the pipeline quarantines, like the cohort reconciliation report.
"""

import statistics
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import numpy as np
from lifelines import KaplanMeierFitter
from lifelines.statistics import logrank_test

from txlens_backend.services.cohorts import CohortRules, DateRange, LabRule
from txlens_backend.survival.analysis import Report
from txlens_backend.survival.service import AnalysisRequest
from txlens_evals.cohort_reconciliation import BAD_ROW_ISSUES, read_csv

TENANT = "center_a"
Patient = dict[str, str]
Tool = Literal["preview_cohort", "run_survival", "sql_query"]
Kind = Literal["count", "value", "percent", "p"]


@dataclass(frozen=True)
class Lab:
    patient_id: str
    day: int
    lab: str
    value: float


@dataclass(frozen=True)
class Truth:
    """Center A's ground truth: patients, outcomes and clean lab results."""

    patients: list[Patient]
    outcomes: dict[str, dict[str, str]]
    labs: list[Lab]

    def where(self, keep: Callable[[Patient], bool]) -> list[Patient]:
        return [p for p in self.patients if keep(p)]

    def count(self, keep: Callable[[Patient], bool]) -> int:
        return len(self.where(keep))

    def lab_values(self, lab: str, first: int, last: int,
                   keep: Callable[[Patient], bool] = lambda p: True) -> list[Lab]:  # fmt: skip
        """Results of one lab between two days after transplant, for patients kept."""
        ids = {p["patient_id"] for p in self.where(keep)}
        return [r for r in self.labs
                if r.lab == lab and first <= r.day <= last and r.patient_id in ids]  # fmt: skip

    def with_lab(self, lab: str, first: int, last: int, test: Callable[[float], bool],
                 keep: Callable[[Patient], bool] = lambda p: True) -> int:  # fmt: skip
        """Patients with at least one qualifying result in the window."""
        return len({r.patient_id for r in self.lab_values(lab, first, last, keep)
                    if test(r.value)})  # fmt: skip

    def graft_loss_day(self, p: Patient) -> int | None:
        loss = self.outcomes[p["patient_id"]]["graft_loss_date"]
        return (date.fromisoformat(loss) - tx(p)).days if loss else None

    def survival(self, keep: Callable[[Patient], bool]) -> tuple[np.ndarray, np.ndarray]:
        """Death-censored graft survival: (days, graft lost) per patient kept."""
        days, events = [], []
        for p in self.where(keep):
            o = self.outcomes[p["patient_id"]]
            loss = self.graft_loss_day(p)
            ends = [date.fromisoformat(o["last_follow_up_date"])]
            ends += [date.fromisoformat(o["death_date"])] if o["death_date"] else []
            days.append(loss if loss is not None else (min(ends) - tx(p)).days)
            events.append(loss is not None)
        return np.array(days, float), np.array(events, bool)


def tx(p: Patient) -> date:
    return date.fromisoformat(p["transplant_date"])


def age(p: Patient) -> int:
    return int(p["transplant_date"][:4]) - int(p["birth_date"][:4])


def load_truth(folder: Path) -> Truth:
    """Center A's patients, outcomes and lab results (quarantined rows left out)."""
    patients = [p for p in read_csv(folder, "patients") if p["tenant_id"] == TENANT]
    ids = {p["patient_id"]: tx(p) for p in patients}
    bad = {r["lab_id"] for r in read_csv(folder, "lab_feed_issues") if r["issue"] in BAD_ROW_ISSUES}
    labs = [
        Lab(r["patient_id"], (datetime.fromisoformat(r["collected_at"]).date()
                              - ids[r["patient_id"]]).days, r["lab"], float(r["value"]))
        for r in read_csv(folder, "labs")
        if r["patient_id"] in ids and r["lab_id"] not in bad and r["value"]
    ]  # fmt: skip
    outcomes = {o["patient_id"]: o for o in read_csv(folder, "outcomes") if o["patient_id"] in ids}
    return Truth(patients, outcomes, labs)


# --- References: the same answer computed on the database without the copilot ---


@dataclass(frozen=True)
class CohortRef:
    rules: CohortRules


@dataclass(frozen=True)
class SurvivalRef:
    request: AnalysisRequest
    pick: Callable[[Report], float | None]


@dataclass(frozen=True)
class SqlRef:
    sql: str  # one row; the answer is its last column


Reference = CohortRef | SurvivalRef | SqlRef


@dataclass(frozen=True)
class DataQuestion:
    qid: str
    text: str
    tool: Tool
    kind: Kind
    truth: Callable[[Truth], float]
    reference: Reference
    tolerance: float = 0.0


def kidney(p: Patient) -> bool:
    return p["organ"] == "kidney"


def heart(p: Patient) -> bool:
    return p["organ"] == "heart"


def km_at(truth: Truth, keep: Callable[[Patient], bool], day: int) -> float:
    days, events = truth.survival(keep)
    fit = KaplanMeierFitter().fit(days, events)
    return 100 * float(fit.survival_function_at_times(day).iloc[0])


def logrank_p(truth: Truth, a: Callable[[Patient], bool], b: Callable[[Patient], bool]) -> float:
    (da, ea), (db, eb) = truth.survival(a), truth.survival(b)
    return float(logrank_test(da, db, ea, eb).p_value)


def group(report: Report, name: str, field: str) -> float | None:
    """A count of one KM group of a report."""
    for curve in report.km:
        if curve.group.lower().startswith(name):
            value = getattr(curve, field)
            return float(value) if isinstance(value, int) else None
    return None


def km_reference(day: int) -> Callable[[Report], float | None]:
    def pick(report: Report) -> float | None:
        points = [p.estimate for p in report.km[0].points if p.day <= day]
        return 100 * points[-1] if points else 100.0

    return pick


def first_year(
    lab: str, days: tuple[int, int] = (0, 365), op: str = ">=", value: float = 0
) -> LabRule:
    return LabRule(lab=lab, op=op, value=value, days_from_transplant=days)  # type: ignore[arg-type]


K, H = CohortRules(organ="kidney"), CohortRules(organ="heart")
M = "copilot.measurement m JOIN copilot.patient p ON p.person_id = m.person_id"
DATA: list[DataQuestion] = [
    # --- cohort counts (preview_cohort) ---
    DataQuestion("c01", "How many kidney transplant recipients does our center have?",
                 "preview_cohort", "count", lambda t: t.count(kidney), CohortRef(K)),
    DataQuestion("c02", "How many heart transplant recipients do we have?",
                 "preview_cohort", "count", lambda t: t.count(heart), CohortRef(H)),
    DataQuestion("c03", "How many kidney recipients received a kidney from a deceased donor?",
                 "preview_cohort", "count",
                 lambda t: t.count(lambda p: kidney(p) and p["donor_type"] == "deceased"),
                 CohortRef(CohortRules(organ="kidney", donor_type="deceased"))),
    DataQuestion("c04", "How many kidney recipients had a living donor?",
                 "preview_cohort", "count",
                 lambda t: t.count(lambda p: kidney(p) and p["donor_type"] == "living"),
                 CohortRef(CohortRules(organ="kidney", donor_type="living"))),
    DataQuestion("c05", "How many heart recipients were 60 or older at transplant?",
                 "preview_cohort", "count", lambda t: t.count(lambda p: heart(p) and age(p) >= 60),
                 CohortRef(CohortRules(organ="heart", age_min=60))),
    DataQuestion("c06", "How many kidney recipients were between 18 and 40 years old at "
                 "transplant?",
                 "preview_cohort", "count",
                 lambda t: t.count(lambda p: kidney(p) and 18 <= age(p) <= 40),
                 CohortRef(CohortRules(organ="kidney", age_min=18, age_max=40))),
    DataQuestion("c07", "How many patients had a dd-cfDNA result of 1% or higher in the first "
                 "year after transplant?", "preview_cohort", "count",
                 lambda t: t.with_lab("dd_cfdna", 0, 365, lambda v: v >= 1.0),
                 CohortRef(CohortRules(lab_rules=[first_year("dd_cfdna", value=1.0)]))),
    DataQuestion("c08", "How many kidney recipients had dd-cfDNA of at least 0.5% within 90 days "
                 "of transplant?", "preview_cohort", "count",
                 lambda t: t.with_lab("dd_cfdna", 0, 90, lambda v: v >= 0.5, kidney),
                 CohortRef(CohortRules(organ="kidney",
                                       lab_rules=[first_year("dd_cfdna", (0, 90), value=0.5)]))),
    DataQuestion("c09", "How many patients had a donor-specific antibody (DSA) MFI of 2000 or more "
                 "in the first year?", "preview_cohort", "count",
                 lambda t: t.with_lab("dsa_mfi", 0, 365, lambda v: v >= 2000),
                 CohortRef(CohortRules(lab_rules=[first_year("dsa_mfi", value=2000)]))),
    DataQuestion("c10", "How many kidney recipients had a tacrolimus trough below 5 ng/mL in the "
                 "first 180 days?", "preview_cohort", "count",
                 lambda t: t.with_lab("tacrolimus_trough", 0, 180, lambda v: v < 5, kidney),
                 CohortRef(CohortRules(organ="kidney", lab_rules=[
                     first_year("tacrolimus_trough", (0, 180), "<", 5)]))),
    DataQuestion("c11", "How many kidney recipients had an eGFR below 30 in their first year?",
                 "preview_cohort", "count",
                 lambda t: t.with_lab("egfr", 0, 365, lambda v: v < 30, kidney),
                 CohortRef(CohortRules(organ="kidney",
                                       lab_rules=[first_year("egfr", op="<", value=30)]))),
    DataQuestion("c12", "How many patients were transplanted in 2023?", "preview_cohort", "count",
                 lambda t: t.count(lambda p: tx(p).year == 2023),
                 CohortRef(CohortRules(transplant_date=DateRange(start=date(2023, 1, 1),
                                                                 end=date(2023, 12, 31))))),
    DataQuestion("c13", "How many kidney recipients transplanted in 2022 had a deceased donor?",
                 "preview_cohort", "count",
                 lambda t: t.count(lambda p: kidney(p) and tx(p).year == 2022
                                   and p["donor_type"] == "deceased"),
                 CohortRef(CohortRules(organ="kidney", donor_type="deceased",
                                       transplant_date=DateRange(start=date(2022, 1, 1),
                                                                 end=date(2022, 12, 31))))),
    DataQuestion("c14", "How many of our transplant recipients have diabetes mellitus?",
                 "preview_cohort", "count", lambda t: t.count(lambda p: p["diabetes"] == "true"),
                 CohortRef(CohortRules(condition_concept_ids=[201820]))),
    DataQuestion("c15", "How many heart recipients have diabetes?", "preview_cohort", "count",
                 lambda t: t.count(lambda p: heart(p) and p["diabetes"] == "true"),
                 CohortRef(CohortRules(organ="heart", condition_concept_ids=[201820]))),
    # --- graft survival (run_survival) ---
    DataQuestion("s01", "In a death-censored graft survival analysis of kidney recipients, how "
                 "many graft losses are there?", "run_survival", "count",
                 lambda t: sum(t.graft_loss_day(p) is not None for p in t.where(kidney)),
                 SurvivalRef(AnalysisRequest(rules=K, landmark_days=0, covariates=[]),
                             lambda r: float(r.events) if isinstance(r.events, int) else None)),
    DataQuestion("s02", "How many heart recipients lost their graft, in a death-censored graft "
                 "survival analysis?", "run_survival", "count",
                 lambda t: sum(t.graft_loss_day(p) is not None for p in t.where(heart)),
                 SurvivalRef(AnalysisRequest(rules=H, landmark_days=0, covariates=[]),
                             lambda r: float(r.events) if isinstance(r.events, int) else None)),
    DataQuestion("s03", "Compare graft survival by donor type for kidney recipients: how many "
                 "graft losses were there in the deceased-donor group?", "run_survival", "count",
                 lambda t: sum(t.graft_loss_day(p) is not None for p in t.where(
                     lambda p: kidney(p) and p["donor_type"] == "deceased")),
                 SurvivalRef(AnalysisRequest(rules=K, grouping="donor_type", landmark_days=0,
                                             covariates=[]),
                             lambda r: group(r, "deceased", "events"))),
    DataQuestion("s04", "What is the log-rank p-value comparing graft survival between living- and "
                 "deceased-donor kidney recipients?", "run_survival", "p",
                 lambda t: logrank_p(t, lambda p: kidney(p) and p["donor_type"] == "living",
                                     lambda p: kidney(p) and p["donor_type"] == "deceased"),
                 SurvivalRef(AnalysisRequest(rules=K, grouping="donor_type", landmark_days=0,
                                             covariates=[]),
                             lambda r: r.log_rank.p_value if r.log_rank else None), 0.02),
    DataQuestion("s05", "What is the 1-year graft survival (death-censored) of all our transplant "
                 "recipients, in percent?", "run_survival", "percent",
                 lambda t: km_at(t, lambda p: True, 365),
                 SurvivalRef(AnalysisRequest(landmark_days=0, covariates=[]), km_reference(365)),
                 2.0),
    DataQuestion("s06", "In a graft survival analysis by organ, how many heart recipients are "
                 "included?", "run_survival", "count", lambda t: t.count(heart),
                 SurvivalRef(AnalysisRequest(grouping="organ", landmark_days=0, covariates=[]),
                             lambda r: group(r, "heart", "n"))),
    DataQuestion("s07", "How many graft losses were there among recipients aged 60 or older at "
                 "transplant (death-censored graft survival)?", "run_survival", "count",
                 lambda t: sum(t.graft_loss_day(p) is not None for p in t.where(
                     lambda p: age(p) >= 60)),
                 SurvivalRef(AnalysisRequest(rules=CohortRules(age_min=60), landmark_days=0,
                                             covariates=[]),
                             lambda r: float(r.events) if isinstance(r.events, int) else None)),
    # --- ad-hoc aggregates (sql_query) ---
    DataQuestion("q01", "What is the median dd-cfDNA value (%) in the first year after transplant "
                 "among kidney recipients?", "sql_query", "value",
                 lambda t: statistics.median(r.value for r in t.lab_values("dd_cfdna", 0, 365,
                                                                           kidney)),
                 SqlRef(f"SELECT count(DISTINCT m.person_id), percentile_cont(0.5) WITHIN GROUP "
                        f"(ORDER BY m.value_as_number) FROM {M} WHERE m.lab = 'dd_cfdna' AND "
                        "m.day BETWEEN 0 AND 365 AND p.organ = 'kidney'"), 0.01),
    DataQuestion("q02", "What is the average tacrolimus trough level in the first 90 days after "
                 "transplant?", "sql_query", "value",
                 lambda t: statistics.mean(r.value for r in
                                           t.lab_values("tacrolimus_trough", 0, 90)),
                 SqlRef("SELECT count(DISTINCT person_id), avg(value_as_number) FROM "
                        "copilot.measurement WHERE lab = 'tacrolimus_trough' AND day BETWEEN 0 "
                        "AND 90"), 0.05),
    DataQuestion("q03", "How many dd-cfDNA results do we have in total?", "sql_query", "count",
                 lambda t: sum(r.lab == "dd_cfdna" for r in t.labs),
                 SqlRef("SELECT count(DISTINCT person_id), count(*) FROM copilot.measurement "
                        "WHERE lab = 'dd_cfdna'")),
    DataQuestion("q04", "How many patients have at least one dd-cfDNA result?", "sql_query",
                 "count", lambda t: len({r.patient_id for r in t.labs if r.lab == "dd_cfdna"}),
                 SqlRef("SELECT count(DISTINCT person_id) FROM copilot.measurement "
                        "WHERE lab = 'dd_cfdna'")),
    DataQuestion("q05", "How many kidney transplants were done in 2024?", "preview_cohort",
                 "count", lambda t: t.count(lambda p: kidney(p) and tx(p).year == 2024),
                 SqlRef("SELECT count(DISTINCT person_id) FROM copilot.patient WHERE organ = "
                        "'kidney' AND transplant_year = 2024")),
    DataQuestion("q06", "What is the average age at transplant of heart recipients?", "sql_query",
                 "value", lambda t: statistics.mean(age(p) for p in t.where(heart)),
                 SqlRef("SELECT count(DISTINCT person_id), avg(age_at_transplant) FROM "
                        "copilot.patient WHERE organ = 'heart'"), 0.1),
    DataQuestion("q07", "How many female kidney recipients are there?", "sql_query", "count",
                 lambda t: t.count(lambda p: kidney(p) and p["sex"] == "F"),
                 SqlRef("SELECT count(DISTINCT person_id) FROM copilot.patient WHERE organ = "
                        "'kidney' AND sex = 'female'")),
    DataQuestion("q08", "What is the average eGFR between day 330 and day 400 after transplant "
                 "among kidney recipients?", "sql_query", "value",
                 lambda t: statistics.mean(r.value for r in t.lab_values("egfr", 330, 400, kidney)),
                 SqlRef(f"SELECT count(DISTINCT m.person_id), avg(m.value_as_number) FROM {M} "
                        "WHERE m.lab = 'egfr' AND m.day BETWEEN 330 AND 400 AND p.organ = "
                        "'kidney'"), 0.2),
    DataQuestion("q09", "How many patients lost their graft within 2 years (730 days) of "
                 "transplant?", "sql_query", "count",
                 lambda t: sum(0 <= (t.graft_loss_day(p) or 10**6) <= 730 for p in t.patients),
                 SqlRef("SELECT count(DISTINCT person_id) FROM copilot.patient WHERE "
                        "graft_loss_day <= 730")),
    DataQuestion("q10", "How many of our transplant recipients have died?", "sql_query", "count",
                 lambda t: sum(bool(o["death_date"]) for o in t.outcomes.values()),
                 SqlRef("SELECT count(DISTINCT person_id) FROM copilot.patient WHERE died")),
    DataQuestion("q11", "What is the median creatinine in the first 30 days after transplant among "
                 "kidney recipients?", "sql_query", "value",
                 lambda t: statistics.median(r.value for r in t.lab_values("creatinine", 0, 30,
                                                                           kidney)),
                 SqlRef(f"SELECT count(DISTINCT m.person_id), percentile_cont(0.5) WITHIN GROUP "
                        f"(ORDER BY m.value_as_number) FROM {M} WHERE m.lab = 'creatinine' AND "
                        "m.day BETWEEN 0 AND 30 AND p.organ = 'kidney'"), 0.02),
    DataQuestion("q12", "What is the standard deviation of tacrolimus trough levels in kidney "
                 "recipients during the first year?", "sql_query", "value",
                 lambda t: statistics.stdev(r.value for r in t.lab_values("tacrolimus_trough", 0,
                                                                          365, kidney)),
                 SqlRef(f"SELECT count(DISTINCT m.person_id), stddev(m.value_as_number) FROM {M} "
                        "WHERE m.lab = 'tacrolimus_trough' AND m.day BETWEEN 0 AND 365 AND "
                        "p.organ = 'kidney'"), 0.05),
    DataQuestion("q13", "How many DSA results were 5000 MFI or higher?", "sql_query", "count",
                 lambda t: sum(r.lab == "dsa_mfi" and r.value >= 5000 for r in t.labs),
                 SqlRef("SELECT count(DISTINCT person_id), count(*) FROM copilot.measurement "
                        "WHERE lab = 'dsa_mfi' AND value_as_number >= 5000")),
    DataQuestion("q14", "What is the average number of days from transplant to graft loss, among "
                 "patients who lost their graft?", "sql_query", "value",
                 lambda t: statistics.mean(d for p in t.patients
                                           if (d := t.graft_loss_day(p)) is not None),
                 SqlRef("SELECT count(DISTINCT person_id), avg(graft_loss_day) FROM "
                        "copilot.patient WHERE graft_loss"), 1.0),
    DataQuestion("q15", "How many male heart recipients are there?", "sql_query", "count",
                 lambda t: t.count(lambda p: heart(p) and p["sex"] == "M"),
                 SqlRef("SELECT count(DISTINCT person_id) FROM copilot.patient WHERE organ = "
                        "'heart' AND sex = 'male'")),
    DataQuestion("q16", "How many patients had a DSA result above 1000 MFI in their first 30 "
                 "days?", "preview_cohort", "count",
                 lambda t: t.with_lab("dsa_mfi", 0, 30, lambda v: v > 1000),
                 CohortRef(CohortRules(lab_rules=[first_year("dsa_mfi", (0, 30), ">", 1000)]))),
    DataQuestion("q17", "What is the mean dd-cfDNA (%) among heart recipients in the first year?",
                 "sql_query", "value",
                 lambda t: statistics.mean(r.value for r in
                                           t.lab_values("dd_cfdna", 0, 365, heart)),
                 SqlRef(f"SELECT count(DISTINCT m.person_id), avg(m.value_as_number) FROM {M} "
                        "WHERE m.lab = 'dd_cfdna' AND m.day BETWEEN 0 AND 365 AND p.organ = "
                        "'heart'"), 0.01),
    DataQuestion("q18", "How many tacrolimus trough results were measured in the first 30 days "
                 "after transplant?", "sql_query", "count",
                 lambda t: len(t.lab_values("tacrolimus_trough", 0, 30)),
                 SqlRef("SELECT count(DISTINCT person_id), count(*) FROM copilot.measurement "
                        "WHERE lab = 'tacrolimus_trough' AND day BETWEEN 0 AND 30")),
]  # fmt: skip
# Sample run: a spread over the three tools.
SAMPLE_DATA = ["c01", "c03", "c07", "c12", "s01", "s04", "q01", "q04", "q07", "q09"]


@dataclass(frozen=True)
class LiteratureQuestion:
    qid: str
    text: str
    answerable: bool = True


LITERATURE = [
    LiteratureQuestion("l01", "What does the literature say about dd-cfDNA thresholds for "
                       "detecting antibody-mediated rejection in kidney transplant recipients?"),
    LiteratureQuestion("l02", "How well does donor-derived cell-free DNA detect acute cellular "
                       "rejection after heart transplantation?"),
    LiteratureQuestion("l03", "How is BK polyomavirus viremia usually managed after kidney "
                       "transplantation?"),
    LiteratureQuestion("l04", "What is the association between de novo donor-specific antibodies "
                       "and kidney graft loss?"),
    LiteratureQuestion("l05", "What role does C4d staining play in diagnosing antibody-mediated "
                       "rejection?"),
    LiteratureQuestion("l06", "What does the literature report about outcomes of borderline "
                       "rejection in kidney transplants?"),
    LiteratureQuestion("l07", "Is tacrolimus intrapatient variability associated with rejection or "
                       "donor-specific antibodies?"),
    LiteratureQuestion("l08", "Can dd-cfDNA be elevated by causes other than rejection, such as BK "
                       "virus nephropathy or infection?"),
    LiteratureQuestion("l09", "What treatments are used for chronic active antibody-mediated "
                       "rejection, and how effective are they?"),
    LiteratureQuestion("l10", "How has the Banff classification of antibody-mediated rejection "
                       "changed in recent updates?"),
    LiteratureQuestion("l11", "What is microvascular inflammation and why does it matter in kidney "
                       "allograft biopsies?"),
    LiteratureQuestion("l12", "How does dd-cfDNA change in the first weeks after transplantation?"),
    LiteratureQuestion("l13", "What is known about pre-transplant donor-specific antibodies and "
                       "desensitization outcomes?"),
    LiteratureQuestion("l14", "Are surveillance (protocol) biopsies useful after kidney "
                       "transplantation?"),
    LiteratureQuestion("l15", "What risk factors for BK polyomavirus nephropathy are reported?"),
    LiteratureQuestion("l16", "How is antibody-mediated rejection diagnosed in heart transplant "
                       "recipients?"),
    LiteratureQuestion("l17", "Does combining dd-cfDNA with DSA testing improve rejection "
                       "detection?"),
    LiteratureQuestion("l18", "What is T cell-mediated rejection and how is it treated in kidney "
                       "recipients?"),
    LiteratureQuestion("l19", "How do HLA eplet mismatches relate to de novo DSA formation?"),
    LiteratureQuestion("l20", "What outcomes are reported for kidney recipients after an episode "
                       "of acute rejection?"),
    LiteratureQuestion("u01", "What is the 10-year survival of pancreatic islet transplants in "
                       "Japan?", answerable=False),
    LiteratureQuestion("u02", "Which smartphone apps improve medication adherence after lung "
                       "transplantation in children?", answerable=False),
    LiteratureQuestion("u03", "What is the recommended rituximab dose for desensitization before "
                       "pediatric liver transplantation?", answerable=False),
]  # fmt: skip
SAMPLE_LITERATURE = ["l01", "l03", "l05", "l08", "l17"]
