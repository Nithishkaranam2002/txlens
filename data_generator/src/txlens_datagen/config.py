"""Generator settings. Every rate and range lives here so the data can be tuned in one place.

The numbers are approximate real-world ranges used for realism only, not clinical guidance.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

Organ = Literal["kidney", "heart"]
RejectionType = Literal["borderline", "TCMR", "ABMR", "mixed"]
InjuryCause = Literal["bk_nephropathy", "cmv_infection", "acute_tubular_injury"]

Range = tuple[float, float]


@dataclass(frozen=True)
class Center:
    """A fictional transplant center. Each center is one tenant in the platform."""

    tenant_id: str
    code: str
    name: str
    weight: float


DEFAULT_CENTERS = (
    Center("center_a", "a", "Aldermoor Transplant Center", 0.40),
    Center("center_b", "b", "Quillfield Medical Center", 0.35),
    Center("center_c", "c", "Vesperhill Kidney and Heart Institute", 0.25),
)


@dataclass(frozen=True)
class DdCfDnaProfile:
    """dd-cfDNA behaviour for one organ (values are % donor-derived cell-free DNA)."""

    stable_median_pct: float
    stable_sigma: float
    thresholds_pct: tuple[float, ...]
    rejection_peak_pct: dict[RejectionType, Range]


@dataclass(frozen=True)
class OrganProfile:
    """Organ-specific rates and lab behaviour."""

    dd_cfdna: DdCfDnaProfile
    rejection_year1_rate: float
    rejection_late_rate: float
    rejection_mix: dict[RejectionType, float]
    creatinine_rise: dict[RejectionType, Range]
    baseline_creatinine_mg_dl: Range
    tacro_target_early: Range
    tacro_target_late: Range
    tacro_early_months: int


KIDNEY = OrganProfile(
    dd_cfdna=DdCfDnaProfile(
        stable_median_pct=0.22,
        stable_sigma=0.35,
        thresholds_pct=(0.5, 1.0),
        rejection_peak_pct={
            "borderline": (0.5, 0.9),
            "TCMR": (1.0, 2.5),
            "ABMR": (1.6, 4.0),
            "mixed": (1.4, 3.5),
        },
    ),
    rejection_year1_rate=0.13,
    rejection_late_rate=0.03,
    rejection_mix={"TCMR": 0.38, "ABMR": 0.22, "borderline": 0.22, "mixed": 0.18},
    creatinine_rise={
        "borderline": (0.10, 0.25),
        "TCMR": (0.25, 0.60),
        "ABMR": (0.30, 0.80),
        "mixed": (0.40, 0.90),
    },
    baseline_creatinine_mg_dl=(0.9, 1.8),
    tacro_target_early=(8.0, 12.0),
    tacro_target_late=(5.0, 8.0),
    tacro_early_months=3,
)

HEART = OrganProfile(
    dd_cfdna=DdCfDnaProfile(
        stable_median_pct=0.06,
        stable_sigma=0.35,
        thresholds_pct=(0.2,),
        rejection_peak_pct={
            "borderline": (0.12, 0.25),
            "TCMR": (0.25, 0.8),
            "ABMR": (0.4, 1.5),
            "mixed": (0.5, 1.5),
        },
    ),
    rejection_year1_rate=0.16,
    rejection_late_rate=0.03,
    rejection_mix={"TCMR": 0.50, "ABMR": 0.15, "borderline": 0.25, "mixed": 0.10},
    creatinine_rise={
        "borderline": (0.0, 0.05),
        "TCMR": (0.0, 0.10),
        "ABMR": (0.05, 0.15),
        "mixed": (0.05, 0.20),
    },
    baseline_creatinine_mg_dl=(0.8, 1.3),
    tacro_target_early=(10.0, 15.0),
    tacro_target_late=(8.0, 10.0),
    tacro_early_months=6,
)


@dataclass(frozen=True)
class InjuryProfile:
    """A non-rejection cause of graft injury that also raises dd-cfDNA (a false alarm)."""

    rate: float
    biopsy_day: tuple[int, int]
    dd_cfdna_peak_pct: Range
    creatinine_rise: Range


@dataclass(frozen=True)
class SurveillanceConfig:
    """dd-cfDNA surveillance schedule: listed months in year 1, then every `interval` months."""

    months_year1: tuple[int, ...] = (1, 2, 3, 4, 6, 9, 12)
    interval_after_months: int = 3
    missed_rate: float = 0.07
    jitter_days: int = 5


@dataclass(frozen=True)
class MessConfig:
    """How often each kind of problem is injected into the raw lab feeds."""

    duplicate_rate: float = 0.02
    bad_date_rate: float = 0.01
    name_variant_rate: float = 0.15
    whitespace_rate: float = 0.02
    missing_value_rate: float = 0.005
    loinc_coded_rate: float = 0.6


@dataclass(frozen=True)
class GeneratorConfig:
    """All knobs for one generator run."""

    seed: int = 42
    n_patients: int = 2000
    centers: tuple[Center, ...] = DEFAULT_CENTERS
    kidney_fraction: float = 0.85
    deceased_donor_fraction: float = 0.65
    age_range: tuple[int, int] = (18, 75)
    transplant_window: tuple[date, date] = (date(2022, 1, 1), date(2025, 12, 31))
    data_cutoff: date = date(2026, 9, 30)
    max_follow_up_days: int = 1096  # 36 months
    diabetes_rate: float = 0.30
    hypertension_rate: float = 0.70
    prior_transplant_rate: float = 0.08
    dgf_rate_deceased: float = 0.25
    dgf_rate_living: float = 0.03
    organs: dict[Organ, OrganProfile] = field(
        default_factory=lambda: {"kidney": KIDNEY, "heart": HEART}
    )
    injuries: dict[InjuryCause, InjuryProfile] = field(
        default_factory=lambda: {
            "bk_nephropathy": InjuryProfile(0.025, (60, 365), (0.6, 1.4), (0.20, 0.50)),
            "cmv_infection": InjuryProfile(0.015, (30, 270), (0.5, 1.1), (0.10, 0.30)),
            "acute_tubular_injury": InjuryProfile(0.015, (21, 120), (0.6, 1.6), (0.30, 0.80)),
        }
    )
    response_rate: dict[RejectionType, float] = field(
        default_factory=lambda: {"borderline": 0.92, "TCMR": 0.85, "ABMR": 0.55, "mixed": 0.60}
    )
    base_graft_loss_3y: float = 0.035
    graft_loss_after_responded_abmr: float = 0.12
    graft_loss_after_unresolved: dict[RejectionType, float] = field(
        default_factory=lambda: {"borderline": 0.30, "TCMR": 0.35, "ABMR": 0.55, "mixed": 0.55}
    )
    death_with_functioning_graft_3y: float = 0.03
    dsa_baseline_positive_rate: float = 0.06
    dsa_positive_in_abmr_rate: float = 0.75
    dsa_positive_mfi: float = 1000.0
    surveillance: SurveillanceConfig = SurveillanceConfig()
    mess: MessConfig = MessConfig()
    pdf_dpi: int = 120
