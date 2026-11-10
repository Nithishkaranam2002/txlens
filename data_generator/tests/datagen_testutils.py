"""Small helpers shared by the tests."""

from txlens_datagen.models import LabName, PatientRecord


def values(records: list[PatientRecord], lab: LabName) -> list[float]:
    """All values of one lab across records."""
    return [r.value for rec in records for r in rec.labs if r.lab == lab]


def fraction(flags: list[bool]) -> float:
    """Share of True values."""
    return sum(flags) / len(flags)


def is_stable(record: PatientRecord) -> bool:
    """No episodes, no DGF and no graft loss: labs should sit in the stable range."""
    return not (
        record.rejections
        or record.injuries
        or record.patient.dgf
        or record.outcome.graft_loss_date is not None
    )
