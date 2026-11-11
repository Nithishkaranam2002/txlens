"""Build the full ground-truth record for one patient."""

from typing import cast

from txlens_datagen.cohort import make_patient, on_day
from txlens_datagen.config import GeneratorConfig, InjuryCause, RejectionType
from txlens_datagen.course import Course, EpisodePlan, simulate_course
from txlens_datagen.labs import make_labs
from txlens_datagen.meds import make_med_orders
from txlens_datagen.models import (
    Biopsy,
    Encounter,
    EncounterKind,
    InjuryEpisode,
    Outcome,
    Patient,
    PatientRecord,
    RejectionEpisode,
)
from txlens_datagen.notes import make_notes
from txlens_datagen.pathology import (
    BiopsyRequest,
    heart_protocol_days,
    make_biopsy,
    request_for_episode,
    with_adequacy_and_cv,
)
from txlens_datagen.rand import STREAM_PATIENT, Rng, randint, rng_for

CLINIC_MONTHS = (1, 3, 6, 12, 18, 24, 30, 36)
(PART_DEMOGRAPHICS, PART_COURSE, PART_BIOPSY, PART_MEDS, PART_LABS, PART_VISITS, PART_NOTES) = (
    range(7)
)
# Added later, as a new stream, so existing streams (and their data) stay unchanged.
PART_BIOPSY_DETAIL = 7


def patient_key(index: int) -> str:
    """Short, stable per-patient key used inside record ids."""
    return f"{index + 1:06d}"


def episode_records(
    patient: Patient, course: Course, key: str, plan: EpisodePlan, number: int, biopsy_id: str
) -> RejectionEpisode | InjuryEpisode:
    """Ground-truth record for one episode."""
    episode_id = f"EP-{key}-{number}"
    dsa_from = course.dsa_positive_from_day
    if plan.is_rejection:
        return RejectionEpisode(
            episode_id=episode_id,
            patient_id=patient.patient_id,
            rejection_type=cast(RejectionType, plan.kind),
            signal_onset_date=on_day(patient, plan.onset_day),
            biopsy_date=on_day(patient, plan.biopsy_day),
            biopsy_id=biopsy_id,
            peak_dd_cfdna_pct=plan.peak_dd_cfdna_pct,
            creatinine_rise_fraction=plan.creatinine_rise,
            treatment=plan.treatment,
            responded=plan.resolves,
            dsa_positive=dsa_from is not None and dsa_from <= plan.biopsy_day,
        )
    return InjuryEpisode(
        episode_id=episode_id,
        patient_id=patient.patient_id,
        cause=cast(InjuryCause, plan.kind),
        onset_date=on_day(patient, plan.onset_day),
        biopsy_date=on_day(patient, plan.biopsy_day),
        biopsy_id=biopsy_id,
        peak_dd_cfdna_pct=plan.peak_dd_cfdna_pct,
        creatinine_rise_fraction=plan.creatinine_rise,
        treatment=plan.treatment,
    )


def add_episodes_and_biopsies(record: PatientRecord, course: Course, key: str, rng: Rng) -> None:
    """Episodes, their confirming biopsies, and heart protocol biopsies, numbered by date."""
    patient = record.patient
    slots: list[tuple[int, EpisodePlan | None]] = [(p.biopsy_day, p) for p in course.episodes]
    if patient.organ == "heart":
        slots += [(day, None) for day in heart_protocol_days(course, rng)]
    for number, (day, plan) in enumerate(sorted(slots, key=lambda s: s[0]), start=1):
        biopsy_id = f"BX-{key}-{number:02d}"
        if plan is None:
            req = BiopsyRequest(biopsy_id, day, "protocol", "no_rejection", "", "", "")
        else:
            episode = episode_records(patient, course, key, plan, number, biopsy_id)
            if isinstance(episode, RejectionEpisode):
                record.rejections.append(episode)
            else:
                record.injuries.append(episode)
            req = request_for_episode(plan, biopsy_id, episode.episode_id)
        record.biopsies.append(make_biopsy(req, patient, rng))


def make_outcome(patient: Patient, course: Course) -> Outcome:
    """Graft loss and death as observed by the end of follow-up."""
    loss, death = course.graft_loss_day, course.death_day
    return Outcome(
        patient_id=patient.patient_id,
        last_follow_up_date=on_day(patient, course.end_day),
        graft_loss_date=None if loss is None else on_day(patient, loss),
        graft_loss_cause=course.graft_loss_cause,
        death_date=None if death is None else on_day(patient, death),
        death_with_functioning_graft=death is not None and (loss is None or death < loss),
    )


def make_encounters(
    patient: Patient, course: Course, biopsies: list[Biopsy], key: str, rng: Rng
) -> list[Encounter]:
    """Clinic visits, biopsy procedures and rejection-treatment admissions."""
    items: list[tuple[EncounterKind, int, int]] = []
    for month in CLINIC_MONTHS:
        day = round(month * 30.4375) + randint(rng, -7, 7)
        if day <= course.end_day:
            items.append(("clinic", day, day))
    for biopsy in biopsies:
        day = (biopsy.biopsy_date - patient.transplant_date).days
        items.append(("biopsy", day, day))
    for plan in course.episodes:
        if plan.is_rejection:
            items.append(("admission", plan.biopsy_day, plan.biopsy_day + randint(rng, 3, 5)))
    items.sort(key=lambda item: (item[1], item[0]))
    return [
        Encounter(
            f"E-{key}-{n:03d}",
            patient.patient_id,
            kind,
            on_day(patient, start),
            on_day(patient, end),
        )
        for n, (kind, start, end) in enumerate(items, start=1)
    ]


def build_record(index: int, config: GeneratorConfig, *, with_notes: bool = True) -> PatientRecord:
    """Simulate one patient end to end. Depends only on (seed, index), not on cohort size."""

    def rng(part: int) -> Rng:
        return rng_for(config.seed, STREAM_PATIENT, index, part)

    key = patient_key(index)
    patient = make_patient(index, config, rng(PART_DEMOGRAPHICS))
    course = simulate_course(patient, config, rng(PART_COURSE))
    record = PatientRecord(patient=patient, outcome=make_outcome(patient, course))
    add_episodes_and_biopsies(record, course, key, rng(PART_BIOPSY))
    detail = rng(PART_BIOPSY_DETAIL)
    record.biopsies = [with_adequacy_and_cv(b, patient, detail) for b in record.biopsies]
    record.meds = make_med_orders(patient, course, key, rng(PART_MEDS))
    record.labs, record.schedule = make_labs(patient, course, config, key, rng(PART_LABS))
    record.encounters = make_encounters(patient, course, record.biopsies, key, rng(PART_VISITS))
    if with_notes:
        record.notes = make_notes(record, config, key, rng(PART_NOTES))
    return record
