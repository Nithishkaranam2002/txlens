"""Biopsy findings: Banff lesion scores for kidney, ISHLT grades for heart."""

from dataclasses import dataclass
from typing import cast

from txlens_datagen.cohort import on_day
from txlens_datagen.course import Course, EpisodePlan
from txlens_datagen.models import Biopsy, Diagnosis, Indication, Patient
from txlens_datagen.rand import Rng, chance, pick, randint

HEART_PROTOCOL_MONTHS = (1, 3, 6, 12)
OTHER_FINDINGS = {
    "bk_nephropathy": "Polyomavirus (BK) nephropathy, SV40 immunostain positive",
    "cmv_infection": "CMV immunostain positive in rare tubular cells",
    "acute_tubular_injury": "Acute tubular injury",
}


@dataclass(frozen=True)
class BiopsyRequest:
    """What a biopsy should show, before its lesion scores are drawn."""

    biopsy_id: str
    day: int
    indication: Indication
    diagnosis: Diagnosis
    tcmr_grade: str
    other_finding: str
    episode_id: str


def request_for_episode(plan: EpisodePlan, biopsy_id: str, episode_id: str) -> BiopsyRequest:
    """The for-cause biopsy that confirms (or rules out) rejection for an episode."""
    diagnosis = cast(Diagnosis, plan.kind) if plan.is_rejection else "no_rejection"
    return BiopsyRequest(
        biopsy_id=biopsy_id,
        day=plan.biopsy_day,
        indication="for_cause",
        diagnosis=diagnosis,
        tcmr_grade=plan.tcmr_grade,
        other_finding="" if plan.is_rejection else OTHER_FINDINGS[plan.kind],
        episode_id=episode_id,
    )


def heart_protocol_days(course: Course, rng: Rng) -> list[int]:
    """Routine heart surveillance biopsy days that do not clash with an episode biopsy."""
    days = []
    for month in HEART_PROTOCOL_MONTHS:
        day = round(month * 30.4375) + randint(rng, -7, 7)
        near_episode = any(abs(day - e.biopsy_day) < 30 for e in course.episodes)
        if day <= course.end_day and not near_episode:
            days.append(day)
    return days


def scores(rng: Rng, low: int, high: int) -> int:
    """A lesion score in [low, high]."""
    return randint(rng, low, high)


def kidney_biopsy(req: BiopsyRequest, patient: Patient, rng: Rng) -> Biopsy:
    """Banff lesion scores consistent with the diagnosis."""
    chronic = (0, 1) if req.day < 365 else (1, 2)
    i = t = v = g = ptc = c4d = 0
    if req.other_finding.startswith("Polyomavirus"):
        i, t = scores(rng, 1, 2), scores(rng, 1, 2)  # BK virus can mimic rejection
    if req.diagnosis == "borderline":
        i, t = 1, 1
    if req.diagnosis in ("TCMR", "mixed"):
        i = scores(rng, 2, 3)
        t = 3 if req.tcmr_grade == "IB" else 2
        v = 1 if req.tcmr_grade == "IIA" else 0
    if req.diagnosis in ("ABMR", "mixed"):
        g, ptc = scores(rng, 1, 3), scores(rng, 1, 3)
        c4d = scores(rng, 2, 3) if chance(rng, 0.6) else scores(rng, 0, 1)
        if req.diagnosis == "ABMR":
            i, t = scores(rng, 0, 1), scores(rng, 0, 1)
    return Biopsy(
        biopsy_id=req.biopsy_id,
        patient_id=patient.patient_id,
        organ="kidney",
        biopsy_date=on_day(patient, req.day),
        indication=req.indication,
        diagnosis=req.diagnosis,
        other_finding=req.other_finding,
        episode_id=req.episode_id,
        banff_i=i,
        banff_t=t,
        banff_v=v,
        banff_g=g,
        banff_ptc=ptc,
        banff_ci=scores(rng, *chronic),
        banff_ct=scores(rng, *chronic),
        c4d=c4d,
        banff_tcmr_grade=req.tcmr_grade,
    )


def heart_biopsy(req: BiopsyRequest, patient: Patient, rng: Rng) -> Biopsy:
    """ISHLT cellular (ACR) and antibody-mediated (pAMR) grades consistent with the diagnosis."""
    acr, pamr = "0R", "pAMR 0"
    if req.diagnosis == "borderline":
        acr = "1R"
    elif req.diagnosis in ("TCMR", "mixed"):
        acr = "3R" if req.tcmr_grade == "IIA" else "2R"
    if req.diagnosis == "ABMR":
        acr, pamr = pick(rng, ("0R", "1R")), pick(rng, ("pAMR 1 (I+)", "pAMR 2"))
    elif req.diagnosis == "mixed":
        pamr = "pAMR 2"
    return Biopsy(
        biopsy_id=req.biopsy_id,
        patient_id=patient.patient_id,
        organ="heart",
        biopsy_date=on_day(patient, req.day),
        indication=req.indication,
        diagnosis=req.diagnosis,
        other_finding=req.other_finding,
        episode_id=req.episode_id,
        ishlt_acr_grade=acr,
        pamr_grade=pamr,
    )


def make_biopsy(req: BiopsyRequest, patient: Patient, rng: Rng) -> Biopsy:
    """Draw pathology findings for one biopsy."""
    if patient.organ == "kidney":
        return kidney_biopsy(req, patient, rng)
    return heart_biopsy(req, patient, rng)
