"""Longitudinal labs in standard units, driven by the patient's simulated course."""

import math
from dataclasses import dataclass
from datetime import datetime, time

from txlens_datagen.cohort import age_on, on_day
from txlens_datagen.config import GeneratorConfig, OrganProfile
from txlens_datagen.course import Course
from txlens_datagen.models import LAB_UNITS, LabName, LabReason, LabResult, Patient, ScheduledTest
from txlens_datagen.rand import Rng, chance, lognormal_factor, randint, uniform
from txlens_datagen.schedule import DAYS_PER_MONTH, draw_schedule

BASIC_PANEL: tuple[LabName, ...] = ("creatinine", "egfr", "tacrolimus_trough")
FOR_CAUSE_OFFSETS = (-7, 0, 30, 90)
DSA_DAYS = (30, 90, 180, 365, 730, 1095)
REASON_PRIORITY: dict[LabReason, int] = {"routine": 0, "surveillance": 1, "for_cause": 2}
PATIENT_DD_SIGMA = 0.15


def routine_days(end_day: int) -> list[int]:
    """Clinic lab days: weekly in month 1, every 2 weeks to month 3, monthly to year 1, then
    every 2 months."""
    days = [7, 14, 21, 28, 42, 56, 70, 84, *range(120, 366, 30), *range(425, 1096, 60)]
    return [d for d in days if d <= end_day]


def egfr_ckd_epi_2021(creatinine_mg_dl: float, age: int, sex: str) -> float:
    """eGFR (mL/min/1.73m2) from the race-free CKD-EPI 2021 creatinine equation."""
    kappa, alpha = (0.7, -0.241) if sex == "F" else (0.9, -0.302)
    ratio = creatinine_mg_dl / kappa
    value = 142 * math.pow(min(ratio, 1), alpha) * math.pow(max(ratio, 1), -1.2) * 0.9938**age
    return value * (1.012 if sex == "F" else 1.0)


def creatinine_for_egfr(egfr: float, age: int, sex: str) -> float:
    """Invert CKD-EPI 2021: the creatinine that gives a target eGFR."""
    kappa, alpha = (0.7, -0.241) if sex == "F" else (0.9, -0.302)
    scale = 142 * 0.9938**age * (1.012 if sex == "F" else 1.0)
    above = kappa * math.pow(egfr / scale, -1 / 1.2)
    return above if above >= kappa else kappa * math.pow(egfr / scale, 1 / alpha)


@dataclass(frozen=True)
class LabModel:
    """Patient-level lab settings that stay fixed over time."""

    patient: Patient
    course: Course
    profile: OrganProfile
    base_creatinine: float
    dd_median: float
    tacro_bias: float
    dsa_mfi: float


def make_lab_model(patient: Patient, course: Course, config: GeneratorConfig, rng: Rng) -> LabModel:
    """Draw the patient's stable baselines."""
    profile = config.organs[patient.organ]
    low, high = profile.baseline_creatinine_mg_dl
    if patient.organ == "kidney":
        target = min(max(rng.normal(60, 10), 40), 80)
        age = age_on(patient, patient.transplant_date)
        base = min(max(creatinine_for_egfr(target, age, patient.sex), low), high)
    else:
        base = uniform(rng, low, high)
    return LabModel(
        patient=patient,
        course=course,
        profile=profile,
        base_creatinine=base,
        dd_median=profile.dd_cfdna.stable_median_pct * lognormal_factor(rng, PATIENT_DD_SIGMA),
        tacro_bias=lognormal_factor(rng, 0.1),
        dsa_mfi=min(max(4000 * lognormal_factor(rng, 0.6), 1200.0), 20000.0),
    )


def creatinine_on(model: LabModel, day: int, rng: Rng) -> float:
    """Measured creatinine (mg/dL): baseline + episode rises + DGF + heart CNI drift + noise."""
    rise = model.course.creatinine_rise(day)
    if model.patient.dgf:
        rise += 2.5 * math.exp(-day / 8)
    if model.patient.organ == "heart":
        rise += 0.05 * day / 365
    return round(model.base_creatinine * (1 + rise) * lognormal_factor(rng, 0.06), 2)


def tacro_on(model: LabModel, day: int, rng: Rng) -> float:
    """Tacrolimus trough (ng/mL) around the phase target, with occasional out-of-range values."""
    early = day < model.profile.tacro_early_months * DAYS_PER_MONTH
    low, high = model.profile.tacro_target_early if early else model.profile.tacro_target_late
    value = (low + high) / 2 * model.tacro_bias * lognormal_factor(rng, 0.18)
    if chance(rng, 0.04):
        value *= uniform(rng, 0.35, 0.6) if chance(rng, 0.5) else uniform(rng, 1.5, 1.9)
    return round(value, 1)


def dd_cfdna_on(model: LabModel, day: int, rng: Rng) -> float:
    """dd-cfDNA (%): stable level with noise plus any episode excess."""
    draw_sigma = math.sqrt(model.profile.dd_cfdna.stable_sigma**2 - PATIENT_DD_SIGMA**2)
    stable = model.dd_median * lognormal_factor(rng, draw_sigma)
    excess = model.course.dd_cfdna_excess(day) * lognormal_factor(rng, 0.08)
    return max(round(stable + excess, 2), 0.01)


def dsa_on(model: LabModel, day: int, rng: Rng) -> float:
    """Highest donor-specific antibody MFI; >= 1000 counts as positive."""
    start = model.course.dsa_positive_from_day
    if start is not None and day >= start:
        return float(round(model.dsa_mfi * lognormal_factor(rng, 0.15)))
    return float(randint(rng, 0, 800))


def plan_draws(
    model: LabModel, surveillance_days: list[int]
) -> dict[tuple[int, LabName], LabReason]:
    """Which lab is drawn on which day, and why (for-cause beats surveillance beats routine)."""
    end = model.course.end_day
    kidney = model.patient.organ == "kidney"
    wanted: list[tuple[int, LabName, LabReason]] = []
    wanted += [(d, lab, "routine") for d in routine_days(end) for lab in BASIC_PANEL]
    wanted += [(d, "dd_cfdna", "surveillance") for d in surveillance_days]
    wanted += [(d, lab, "routine") for d in surveillance_days for lab in BASIC_PANEL]
    if kidney:
        wanted += [(d, "dsa_mfi", "routine") for d in DSA_DAYS if d <= end]
    for plan in model.course.episodes:
        days = [plan.biopsy_day + off for off in FOR_CAUSE_OFFSETS]
        labs: tuple[LabName, ...] = (*BASIC_PANEL, "dd_cfdna")
        wanted += [(d, lab, "for_cause") for d in days if d <= end for lab in labs]
        if kidney:
            wanted.append((plan.biopsy_day, "dsa_mfi", "for_cause"))
    draws: dict[tuple[int, LabName], LabReason] = {}
    for day, lab, reason in wanted:
        current = draws.get((day, lab))
        if current is None or REASON_PRIORITY[reason] > REASON_PRIORITY[current]:
            draws[(day, lab)] = reason
    return draws


def measure(model: LabModel, lab: LabName, day: int, creatinine: float, rng: Rng) -> float:
    """One measured value in standard units."""
    if lab == "creatinine":
        return creatinine
    if lab == "egfr":
        age = age_on(model.patient, on_day(model.patient, day))
        return float(round(egfr_ckd_epi_2021(creatinine, age, model.patient.sex)))
    if lab == "tacrolimus_trough":
        return tacro_on(model, day, rng)
    if lab == "dd_cfdna":
        return dd_cfdna_on(model, day, rng)
    return dsa_on(model, day, rng)


def make_labs(
    patient: Patient, course: Course, config: GeneratorConfig, key: str, rng: Rng
) -> tuple[list[LabResult], list[ScheduledTest]]:
    """All lab results plus the dd-cfDNA surveillance schedule (with missed tests)."""
    model = make_lab_model(patient, course, config, rng)
    due = draw_schedule(course.end_day, config.surveillance, rng)
    draws = plan_draws(model, [t.day for t in due if not t.missed])
    labs: list[LabResult] = []
    lab_ids: dict[int, str] = {}
    for day in sorted({d for d, _ in draws}):
        when = datetime.combine(
            on_day(patient, day), time(randint(rng, 7, 10), randint(rng, 0, 59))
        )
        creatinine = creatinine_on(model, day, rng)
        for lab in LAB_UNITS:
            if (day, lab) not in draws:
                continue
            lab_id = f"L-{key}-{len(labs) + 1:04d}"
            value = measure(model, lab, day, creatinine, rng)
            labs.append(
                LabResult(
                    lab_id, patient.patient_id, when, lab, value, LAB_UNITS[lab], draws[(day, lab)]
                )
            )
            if lab == "dd_cfdna":
                lab_ids[day] = lab_id
    schedule = [
        ScheduledTest(
            patient.patient_id,
            t.month,
            on_day(patient, t.day),
            not t.missed,
            "" if t.missed else lab_ids[t.day],
        )
        for t in due
    ]
    return labs, schedule
