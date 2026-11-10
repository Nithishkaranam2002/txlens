"""Clinical course: rejection and injury episodes, their lab signals, graft loss and death.

All days are counted from the transplant date (day 0).
"""

import math
from dataclasses import dataclass
from typing import cast

from txlens_datagen.config import GeneratorConfig, InjuryCause, RejectionType
from txlens_datagen.models import Patient
from txlens_datagen.rand import Rng, chance, pick_weighted, randint, uniform

MIN_DAYS_BETWEEN_EPISODES = 90
TCMR_GRADES = {"IA": 0.5, "IB": 0.3, "IIA": 0.2}


@dataclass(frozen=True)
class Bump:
    """A rise in a lab value that starts at `onset_day`, peaks at `peak_day`, then falls or stays.

    For dd-cfDNA `peak` is an absolute excess in %; for creatinine it is a fractional rise.
    """

    onset_day: int
    peak_day: int
    peak: float
    resolves: bool
    decay_days: float
    persist_fraction: float

    def at(self, day: int) -> float:
        """Size of the bump on a given day."""
        if day < self.onset_day:
            return 0.0
        if day <= self.peak_day:
            ramp = (day - self.onset_day) / max(self.peak_day - self.onset_day, 1)
            return self.peak * math.pow(ramp, 1.5)
        if self.resolves:
            return self.peak * math.exp(-(day - self.peak_day) / self.decay_days)
        return self.peak * self.persist_fraction


@dataclass(frozen=True)
class EpisodePlan:
    """One rejection or injury episode before ids are assigned."""

    kind: RejectionType | InjuryCause
    is_rejection: bool
    onset_day: int
    biopsy_day: int
    peak_dd_cfdna_pct: float
    creatinine_rise: float
    resolves: bool
    tcmr_grade: str
    dsa_positive: bool
    treatment: str


@dataclass(frozen=True)
class Course:
    """A patient's simulated course and the lab signals it produces."""

    follow_up_days: int
    end_day: int
    episodes: tuple[EpisodePlan, ...]
    graft_loss_day: int | None
    graft_loss_cause: str | None
    death_day: int | None
    dd_cfdna_bumps: tuple[Bump, ...]
    creatinine_bumps: tuple[Bump, ...]
    dsa_positive_from_day: int | None

    def dd_cfdna_excess(self, day: int) -> float:
        """dd-cfDNA above the patient's stable level on a given day (absolute %)."""
        return sum(b.at(day) for b in self.dd_cfdna_bumps)

    def creatinine_rise(self, day: int) -> float:
        """Fractional creatinine rise above baseline on a given day."""
        return sum(b.at(day) for b in self.creatinine_bumps)


def rejection_treatment(kind: RejectionType, tcmr_grade: str) -> str:
    """Standard-of-care style treatment label for a rejection type."""
    steroids = "methylprednisolone pulse"
    if kind == "borderline":
        return steroids
    if kind == "ABMR":
        return "plasmapheresis + IVIG"
    if kind == "mixed":
        return f"{steroids} + plasmapheresis + IVIG"
    return f"{steroids} + antithymocyte globulin" if tcmr_grade != "IA" else steroids


INJURY_TREATMENT: dict[InjuryCause, str] = {
    "bk_nephropathy": "reduce mycophenolate",
    "cmv_infection": "valganciclovir + reduce mycophenolate",
    "acute_tubular_injury": "supportive care",
}


def draw_rejection(
    biopsy_day: int, patient: Patient, config: GeneratorConfig, rng: Rng
) -> EpisodePlan:
    """One rejection episode with its biopsy on `biopsy_day`."""
    profile = config.organs[patient.organ]
    kind = pick_weighted(rng, profile.rejection_mix)
    lead = randint(rng, 14, min(56, biopsy_day - 14))
    grade = pick_weighted(rng, TCMR_GRADES) if kind in ("TCMR", "mixed") else ""
    is_antibody = kind in ("ABMR", "mixed")
    return EpisodePlan(
        kind=kind,
        is_rejection=True,
        onset_day=biopsy_day - lead,
        biopsy_day=biopsy_day,
        peak_dd_cfdna_pct=round(uniform(rng, *profile.dd_cfdna.rejection_peak_pct[kind]), 2),
        creatinine_rise=round(uniform(rng, *profile.creatinine_rise[kind]), 3),
        resolves=chance(rng, config.response_rate[kind]),
        tcmr_grade=grade,
        dsa_positive=is_antibody and chance(rng, config.dsa_positive_in_abmr_rate),
        treatment=rejection_treatment(kind, grade),
    )


def draw_rejections(
    patient: Patient, follow_up_days: int, config: GeneratorConfig, rng: Rng
) -> list[EpisodePlan]:
    """Year-1 and late rejection episodes."""
    profile = config.organs[patient.organ]
    plans = []
    if chance(rng, profile.rejection_year1_rate):
        plans.append(draw_rejection(randint(rng, 30, 365), patient, config, rng))
    if chance(rng, profile.rejection_late_rate):
        plans.append(
            draw_rejection(randint(rng, 366, config.max_follow_up_days), patient, config, rng)
        )
    return [p for p in plans if p.biopsy_day < follow_up_days]


def draw_injuries(patient: Patient, config: GeneratorConfig, rng: Rng) -> list[EpisodePlan]:
    """Non-rejection injuries (kidney only) that raise dd-cfDNA: BK virus, CMV, ATN."""
    if patient.organ != "kidney":
        return []
    plans = []
    for cause, injury in config.injuries.items():
        if not chance(rng, injury.rate):
            continue
        biopsy_day = randint(rng, *injury.biopsy_day)
        plans.append(
            EpisodePlan(
                kind=cause,
                is_rejection=False,
                onset_day=biopsy_day - randint(rng, 14, min(42, biopsy_day - 7)),
                biopsy_day=biopsy_day,
                peak_dd_cfdna_pct=round(uniform(rng, *injury.dd_cfdna_peak_pct), 2),
                creatinine_rise=round(uniform(rng, *injury.creatinine_rise), 3),
                resolves=chance(rng, 0.9),
                tcmr_grade="",
                dsa_positive=False,
                treatment=INJURY_TREATMENT[cause],
            )
        )
    return plans


def drop_overlaps(plans: list[EpisodePlan]) -> list[EpisodePlan]:
    """Keep episodes in biopsy order, dropping any that start too soon after the previous one."""
    kept: list[EpisodePlan] = []
    for plan in sorted(plans, key=lambda p: p.biopsy_day):
        if not kept or plan.onset_day - kept[-1].biopsy_day >= MIN_DAYS_BETWEEN_EPISODES:
            kept.append(plan)
    return kept


def event_day(rng: Rng, probability: float, first_day: int, last_day: int) -> int | None:
    """Day of an event that happens with `probability` somewhere in [first_day, last_day]."""
    return randint(rng, first_day, last_day) if chance(rng, probability) else None


def loss_probability(plan: EpisodePlan, config: GeneratorConfig) -> float:
    """Chance that this episode leads to graft loss."""
    if not plan.is_rejection:
        return 0.0
    kind = cast(RejectionType, plan.kind)
    if not plan.resolves:
        return config.graft_loss_after_unresolved[kind]
    return config.graft_loss_after_responded_abmr if kind in ("ABMR", "mixed") else 0.0


def episode_bumps(plan: EpisodePlan, stable_pct: float, rng: Rng) -> tuple[Bump, Bump]:
    """dd-cfDNA and creatinine bumps produced by one episode."""
    dd = Bump(
        onset_day=plan.onset_day,
        peak_day=plan.biopsy_day,
        peak=max(plan.peak_dd_cfdna_pct - stable_pct, 0.0),
        resolves=plan.resolves,
        decay_days=uniform(rng, 10, 20),
        persist_fraction=uniform(rng, 0.6, 1.0),
    )
    cr = Bump(
        onset_day=plan.onset_day,
        peak_day=plan.biopsy_day,
        peak=plan.creatinine_rise,
        resolves=plan.resolves,
        decay_days=uniform(rng, 12, 25),
        persist_fraction=uniform(rng, 1.0, 1.3),
    )
    return dd, cr


def simulate_course(patient: Patient, config: GeneratorConfig, rng: Rng) -> Course:
    """Simulate episodes, graft loss and death for one patient."""
    max_days = config.max_follow_up_days
    follow_up = min(max_days, (config.data_cutoff - patient.transplant_date).days)
    death_day = event_day(rng, config.death_with_functioning_graft_3y, 20, max_days)
    loss_day = event_day(rng, config.base_graft_loss_3y, 60, max_days)
    loss_cause = "chronic graft dysfunction" if loss_day is not None else None
    candidates = draw_rejections(patient, follow_up, config, rng) + draw_injuries(
        patient, config, rng
    )

    end = min(follow_up, death_day or max_days, loss_day or max_days)
    kept: list[EpisodePlan] = []
    for plan in drop_overlaps(candidates):
        if plan.biopsy_day >= end:
            continue
        kept.append(plan)
        if chance(rng, loss_probability(plan, config)):
            day = plan.biopsy_day + randint(rng, 60, 540)
            if loss_day is None or day < loss_day:
                loss_day, loss_cause = day, f"rejection ({plan.kind})"
                end = min(end, day)

    return finish_course(patient, config, rng, follow_up, kept, loss_day, loss_cause, death_day)


def finish_course(
    patient: Patient,
    config: GeneratorConfig,
    rng: Rng,
    follow_up: int,
    plans: list[EpisodePlan],
    loss_day: int | None,
    loss_cause: str | None,
    death_day: int | None,
) -> Course:
    """Decide which events were observed during follow-up and build the lab signals."""
    if loss_day is not None and (loss_day > follow_up or (death_day or follow_up + 1) < loss_day):
        loss_day, loss_cause = None, None
    if death_day is not None and death_day > follow_up:
        death_day = None
    if patient.organ == "heart" and loss_day is not None:
        death_day = loss_day
    end = min(d for d in (follow_up, loss_day, death_day) if d is not None)

    stable = config.organs[patient.organ].dd_cfdna.stable_median_pct
    dd_bumps, cr_bumps = [], []
    for plan in plans:
        dd, cr = episode_bumps(plan, stable, rng)
        dd_bumps.append(dd)
        cr_bumps.append(cr)
    if loss_day is not None:
        cr_bumps.append(Bump(max(loss_day - 150, 1), loss_day, uniform(rng, 1.0, 2.5), False, 1, 1))

    return Course(
        follow_up_days=follow_up,
        end_day=end,
        episodes=tuple(plans),
        graft_loss_day=loss_day,
        graft_loss_cause=loss_cause,
        death_day=death_day,
        dd_cfdna_bumps=tuple(dd_bumps),
        creatinine_bumps=tuple(cr_bumps),
        dsa_positive_from_day=dsa_onset(patient, plans, config, rng),
    )


def dsa_onset(
    patient: Patient, plans: list[EpisodePlan], config: GeneratorConfig, rng: Rng
) -> int | None:
    """Day from which donor-specific antibodies are detectable (kidney only), or None."""
    if patient.organ != "kidney":
        return None
    for plan in plans:
        if plan.dsa_positive:
            return max(plan.onset_day - randint(rng, 0, 60), 0)
    if chance(rng, config.dsa_baseline_positive_rate):
        return 0 if chance(rng, 0.5) else randint(rng, 60, config.max_follow_up_days)
    return None
