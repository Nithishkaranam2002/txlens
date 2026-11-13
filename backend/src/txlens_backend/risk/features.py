"""Model features at a surveillance visit, computed only from facts known on or before that day.

The prediction is made once the visit's own results are back, so results dated on the visit
day are used. dd-cfDNA history uses scheduled visits only (an extra draw is often for-cause and
says more about a clinician's worry than about the graft); the other labs use every result.
"""

import math
import statistics
from dataclasses import dataclass
from datetime import date, timedelta

from txlens_backend.risk.record import Finding, PatientRecord
from txlens_backend.risk.visits import scheduled_visits
from txlens_backend.surveillance.config import Schedule

DSA_POSITIVE_MFI = 1000.0  # same cut-off as the surveillance config and the survival extract
DD_CFDNA_FLOOR = 0.01  # % (results below the assay's reporting limit are logged at the floor)
BASELINE_DAYS = (30, 90)  # creatinine baseline: days after transplant, once the graft settles
TACROLIMUS_WINDOW = timedelta(days=180)
TACROLIMUS_MIN_POINTS = 3
DAYS_PER_MONTH = 30.4375


@dataclass(frozen=True)
class Feature:
    """One model input, described for the model card and the patient page."""

    name: str
    label: str
    unit: str
    description: str


FEATURES = (
    Feature("dd_cfdna_log", "dd-cfDNA (log)", "log %", "Natural log of the visit's dd-cfDNA."),
    Feature("dd_cfdna_slope", "dd-cfDNA trend", "% per month",
            "Change since the previous scheduled dd-cfDNA, per month."),
    Feature("dd_cfdna_vs_min", "dd-cfDNA vs own lowest", "ratio",
            "Visit dd-cfDNA divided by the patient's lowest earlier scheduled value."),
    Feature("creatinine_change_pct", "Creatinine change", "%",
            "Latest creatinine vs the median of days 30-90 after transplant."),
    Feature("tacrolimus_cv", "Tacrolimus variability", "CV %",
            "Coefficient of variation of tacrolimus troughs in the last 180 days."),
    Feature("dsa_positive", "DSA positive", "yes/no",
            "Any donor-specific antibody result with MFI >= 1000 so far."),
    Feature("months_since_transplant", "Time since transplant", "months",
            "Months since transplant."),
    Feature("prior_rejection", "Prior rejection", "yes/no",
            "A biopsy-proven rejection recorded before this visit."),
    Feature("organ_heart", "Heart graft", "yes/no", "Heart (1) or kidney (0) transplant."),
    Feature("donor_deceased", "Deceased donor", "yes/no", "Deceased (1) or living (0) donor."),
    Feature("dgf", "Delayed graft function", "yes/no",
            "Delayed graft function recorded on or before this visit."),
)  # fmt: skip
FEATURE_NAMES = tuple(f.name for f in FEATURES)
NAN = math.nan


def latest_on(values: list[tuple[date, float]], day: date) -> float | None:
    """The last value dated `day` (several draws on one day: the last one wins)."""
    same_day = [v for d, v in values if d == day]
    return same_day[-1] if same_day else None


def dd_cfdna(record: PatientRecord, schedule: Schedule, visit: date) -> tuple[float, float, float]:
    """(log level, slope per month, ratio to own earlier minimum) from scheduled draws."""
    draws = [(o.day, o.value) for o in record.values(schedule.marker, visit)]
    visits = scheduled_visits(record, schedule, visit)
    level = latest_on(draws, visit)
    if level is None:
        return NAN, NAN, NAN
    earlier = [v for d in visits if d < visit for v in [latest_on(draws, d)] if v is not None]
    earlier_days = [d for d in visits if d < visit]
    slope = NAN
    if earlier:
        months = (visit - earlier_days[-1]).days / DAYS_PER_MONTH
        slope = (level - earlier[-1]) / months if months > 0 else NAN
    ratio = level / max(min(earlier), DD_CFDNA_FLOOR) if earlier else NAN
    return math.log(max(level, DD_CFDNA_FLOOR)), slope, ratio


def creatinine_change(record: PatientRecord, visit: date) -> float:
    """% change of the latest creatinine from the baseline median (NaN without both)."""
    values = record.values("creatinine", visit)
    if not values:
        return NAN
    start = record.transplant_day + timedelta(days=BASELINE_DAYS[0])
    end = record.transplant_day + timedelta(days=BASELINE_DAYS[1])
    baseline = [o.value for o in values[:-1] if start <= o.day <= end]
    if not baseline:
        return NAN
    median = statistics.median(baseline)
    return 100.0 * (values[-1].value - median) / median if median > 0 else NAN


def tacrolimus_cv(record: PatientRecord, visit: date) -> float:
    """Coefficient of variation (%) of recent troughs (NaN with fewer than 3)."""
    troughs = [o.value for o in record.values("tacrolimus_trough", visit)
               if o.day > visit - TACROLIMUS_WINDOW]  # fmt: skip
    if len(troughs) < TACROLIMUS_MIN_POINTS:
        return NAN
    mean = statistics.fmean(troughs)
    return 100.0 * statistics.stdev(troughs) / mean if mean > 0 else NAN


def flag(value: bool) -> float:
    return 1.0 if value else 0.0


def features_at(record: PatientRecord, schedule: Schedule, visit: date) -> dict[str, float]:
    """The feature vector at a visit. Reads only facts dated on or before `visit` (biopsy
    results only when recorded strictly before it)."""
    level, slope, ratio = dd_cfdna(record, schedule, visit)
    dsa = any(o.value >= DSA_POSITIVE_MFI for o in record.values("dsa_mfi", visit))
    prior = any(r.finding is Finding.REJECTION and r.known_day < visit for r in record.results)
    dgf = record.dgf_known_day is not None and record.dgf_known_day <= visit
    return {
        "dd_cfdna_log": level,
        "dd_cfdna_slope": slope,
        "dd_cfdna_vs_min": ratio,
        "creatinine_change_pct": creatinine_change(record, visit),
        "tacrolimus_cv": tacrolimus_cv(record, visit),
        "dsa_positive": flag(dsa),
        "months_since_transplant": (visit - record.transplant_day).days / DAYS_PER_MONTH,
        "prior_rejection": flag(prior),
        "organ_heart": flag(record.organ == "heart"),
        "donor_deceased": flag(record.donor_type != "living"),
        "dgf": flag(dgf and record.organ == "kidney"),
    }
