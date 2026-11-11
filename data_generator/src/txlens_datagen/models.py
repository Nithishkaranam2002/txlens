"""Ground-truth records. The generator builds these first, then renders them into raw files."""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from txlens_datagen.config import InjuryCause, Organ, RejectionType

Sex = Literal["F", "M"]
DonorType = Literal["deceased", "living"]
Diagnosis = Literal["no_rejection", "borderline", "TCMR", "ABMR", "mixed"]
LabName = Literal["creatinine", "egfr", "tacrolimus_trough", "dd_cfdna", "dsa_mfi"]
LabReason = Literal["routine", "surveillance", "for_cause"]
Indication = Literal["protocol", "for_cause"]
Adequacy = Literal["adequate", "marginal", "inadequate"]
# A finding the pathologist left out of the report (ground truth for "the model must not guess").
NotReported = Literal["not_reported"]
EncounterKind = Literal["clinic", "biopsy", "admission"]
Temporality = Literal["current", "historical", "hypothetical"]
Experiencer = Literal["patient", "family"]

LAB_UNITS: dict[LabName, str] = {
    "creatinine": "mg/dL",
    "egfr": "mL/min/1.73m2",
    "tacrolimus_trough": "ng/mL",
    "dd_cfdna": "%",
    "dsa_mfi": "MFI",
}


@dataclass(frozen=True)
class Patient:
    """Demographics and transplant facts for one synthetic patient."""

    patient_id: str
    tenant_id: str
    mrn: str
    first_name: str
    last_name: str
    sex: Sex
    birth_date: date
    organ: Organ
    donor_type: DonorType
    transplant_date: date
    primary_disease: str
    diabetes: bool
    hypertension: bool
    prior_transplant: bool
    dgf: bool


@dataclass(frozen=True)
class RejectionEpisode:
    """One biopsy-proven rejection episode, with its signal and treatment response."""

    episode_id: str
    patient_id: str
    rejection_type: RejectionType
    signal_onset_date: date
    biopsy_date: date
    biopsy_id: str
    peak_dd_cfdna_pct: float
    creatinine_rise_fraction: float
    treatment: str
    responded: bool
    dsa_positive: bool


@dataclass(frozen=True)
class InjuryEpisode:
    """A non-rejection graft injury that raises dd-cfDNA: a realistic false alarm."""

    episode_id: str
    patient_id: str
    cause: InjuryCause
    onset_date: date
    biopsy_date: date
    biopsy_id: str
    peak_dd_cfdna_pct: float
    creatinine_rise_fraction: float
    treatment: str
    is_rejection: bool = False


@dataclass(frozen=True)
class Outcome:
    """End-of-follow-up status for one patient."""

    patient_id: str
    last_follow_up_date: date
    graft_loss_date: date | None
    graft_loss_cause: str | None
    death_date: date | None
    death_with_functioning_graft: bool


@dataclass(frozen=True)
class Biopsy:
    """One graft biopsy and its pathology findings (Banff for kidney, ISHLT for heart)."""

    biopsy_id: str
    patient_id: str
    organ: Organ
    biopsy_date: date
    indication: Indication
    diagnosis: Diagnosis
    other_finding: str
    episode_id: str
    banff_i: int | None = None
    banff_t: int | None = None
    banff_v: int | None = None
    banff_g: int | None = None
    banff_ptc: int | None = None
    banff_ci: int | None = None
    banff_ct: int | None = None
    c4d: int | None = None
    banff_cv: int | NotReported | None = None
    adequacy: Adequacy | NotReported | None = None
    banff_tcmr_grade: str = ""
    ishlt_acr_grade: str = ""
    pamr_grade: str = ""


@dataclass(frozen=True)
class MedOrder:
    """One medication order (a dose change starts a new order)."""

    order_id: str
    patient_id: str
    drug: str
    dose_value: float
    dose_unit: str
    route: str
    frequency: str
    start_date: date
    end_date: date | None
    rxnorm: str
    reason: str


@dataclass(frozen=True)
class LabResult:
    """One lab result in standard units."""

    lab_id: str
    patient_id: str
    collected_at: datetime
    lab: LabName
    value: float
    unit: str
    reason: LabReason


@dataclass(frozen=True)
class ScheduledTest:
    """One due dd-cfDNA surveillance test and whether it was done."""

    patient_id: str
    month: int
    due_date: date
    done: bool
    lab_id: str


@dataclass(frozen=True)
class Encounter:
    """A clinic visit, biopsy procedure or admission."""

    encounter_id: str
    patient_id: str
    kind: EncounterKind
    start_date: date
    end_date: date


@dataclass(frozen=True)
class NoteFact:
    """One fact mentioned in a note, with its exact character span and context flags."""

    note_id: str
    concept: str
    category: str
    text: str
    start: int
    end: int
    negated: bool
    temporality: Temporality
    experiencer: Experiencer
    value: float | None = None
    unit: str | None = None


@dataclass(frozen=True)
class Note:
    """A free-text clinical note and the facts it contains."""

    note_id: str
    patient_id: str
    encounter_id: str
    note_date: date
    note_type: str
    text: str
    facts: tuple[NoteFact, ...]


@dataclass
class PatientRecord:
    """Everything known about one patient: the full ground truth."""

    patient: Patient
    outcome: Outcome
    rejections: list[RejectionEpisode] = field(default_factory=list)
    injuries: list[InjuryEpisode] = field(default_factory=list)
    biopsies: list[Biopsy] = field(default_factory=list)
    meds: list[MedOrder] = field(default_factory=list)
    labs: list[LabResult] = field(default_factory=list)
    schedule: list[ScheduledTest] = field(default_factory=list)
    encounters: list[Encounter] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
