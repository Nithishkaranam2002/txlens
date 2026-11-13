"""Hand-built patient records for risk model tests."""

from datetime import date, timedelta

from txlens_backend.risk.record import BiopsyResult, Finding, PatientRecord
from txlens_backend.surveillance.config import Schedule
from txlens_backend.surveillance.model import Observation

TX = date(2024, 1, 1)
SCHEDULE = Schedule(marker="dd_cfdna", months=(1, 2, 3), then_every_months=3, tolerance_days=14)


def day(n: int) -> date:
    """`n` days after transplant."""
    return TX + timedelta(days=n)


def obs(*points: tuple[int, float]) -> tuple[Observation, ...]:
    return tuple(Observation(day(d), v) for d, v in points)


def record(**changes: object) -> PatientRecord:
    """A kidney recipient followed for two years with a full set of labs."""
    base: dict[str, object] = {
        "key": "center_a:1", "tenant": "center_a", "organ": "kidney", "donor_type": "living",
        "sex": "female", "birth_year": 1980, "transplant_day": TX, "follow_up_end": day(730),
        "end_day": None,
        "observations": {
            "dd_cfdna": obs((30, 0.2), (61, 0.3), (91, 0.6), (183, 1.2), (274, 0.4)),
            "creatinine": obs((35, 1.0), (60, 1.2), (85, 1.1), (180, 1.65)),
            "tacrolimus_trough": obs((40, 8.0), (100, 10.0), (150, 6.0), (180, 8.0)),
            "dsa_mfi": obs((30, 200.0), (180, 1500.0)),
        },
        "biopsy_days": (day(200),),
        "results": (BiopsyResult(day(200), day(205), Finding.REJECTION),),
        "dgf_known_day": None,
    }  # fmt: skip
    return PatientRecord(**(base | changes))  # type: ignore[arg-type]
