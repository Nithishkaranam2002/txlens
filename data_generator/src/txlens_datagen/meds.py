"""Medication orders: maintenance immunosuppression plus rejection and infection treatment."""

from dataclasses import dataclass, field

from txlens_datagen.cohort import on_day
from txlens_datagen.course import Course
from txlens_datagen.models import MedOrder, Patient
from txlens_datagen.rand import Rng, pick, pick_weighted

# RxNorm ingredient codes for the core drugs. Other drugs are left uncoded on purpose:
# they get mapped (or sent to review) in the OMOP step.
RXNORM = {
    "tacrolimus": "42316",
    "mycophenolate mofetil": "68149",
    "prednisone": "8640",
    "methylprednisolone": "6902",
}
TACRO_STEP_DAYS = (0, 30, 90, 180, 365)
PREDNISONE_TAPER = ((0, 20.0), (14, 15.0), (30, 10.0), (60, 7.5), (90, 5.0))
MMF_DOSES = {1000.0: 0.7, 750.0: 0.1, 500.0: 0.2}


@dataclass
class OrderBook:
    """Collects orders for one patient and numbers them."""

    patient: Patient
    key: str
    stop_day: int | None
    orders: list[MedOrder] = field(default_factory=list)

    def add(
        self,
        drug: str,
        dose: tuple[float, str],
        route: str,
        frequency: str,
        days: tuple[int, int | None],
        reason: str,
    ) -> None:
        """Add one order running from days[0] to days[1] (None = still active)."""
        start, end = days
        if self.stop_day is not None:
            end = self.stop_day if end is None else min(end, self.stop_day)
        self.orders.append(
            MedOrder(
                order_id=f"RX-{self.key}-{len(self.orders) + 1:03d}",
                patient_id=self.patient.patient_id,
                drug=drug,
                dose_value=dose[0],
                dose_unit=dose[1],
                route=route,
                frequency=frequency,
                start_date=on_day(self.patient, start),
                end_date=None if end is None else on_day(self.patient, end),
                rxnorm=RXNORM.get(drug, ""),
                reason=reason,
            )
        )


def add_stepped(
    book: OrderBook, drug: str, steps: list[tuple[int, float]], frequency: str, end_day: int
) -> None:
    """Add one order per dose step; each step ends the day before the next starts."""
    steps = [s for s in steps if s[0] <= end_day]
    for (day, dose), nxt in zip(steps, [*steps[1:], None], strict=True):
        book.add(
            drug, (dose, "mg"), "oral", frequency, (day, nxt[0] - 1 if nxt else None), "maintenance"
        )


def tacrolimus_steps(rng: Rng) -> list[tuple[int, float]]:
    """Tacrolimus dose (mg BID) falling over the first year as trough targets drop."""
    dose = pick(rng, (3.0, 3.5, 4.0, 4.5, 5.0))
    steps = []
    for day in TACRO_STEP_DAYS:
        steps.append((day, dose))
        dose = max(1.0, dose - pick(rng, (0.0, 0.5, 1.0)))
    return steps


def mmf_steps(course: Course, rng: Rng) -> list[tuple[int, float]]:
    """Mycophenolate dose (mg BID), reduced to 500 mg during BK or CMV infection."""
    steps = [(0, pick_weighted(rng, MMF_DOSES))]
    for plan in course.episodes:
        if plan.kind in ("bk_nephropathy", "cmv_infection") and steps[-1][1] > 500:
            steps.append((plan.biopsy_day, 500.0))
    return steps


def add_episode_treatment(book: OrderBook, course: Course) -> None:
    """Short treatment courses for rejection (steroids, ATG, IVIG) and CMV (valganciclovir)."""
    for plan in course.episodes:
        day = plan.biopsy_day
        if "methylprednisolone" in plan.treatment:
            dose = 250.0 if plan.kind == "borderline" else 500.0
            book.add("methylprednisolone", (dose, "mg"), "IV", "daily", (day, day + 2), "rejection")
        if "antithymocyte" in plan.treatment:
            book.add(
                "antithymocyte globulin", (1.5, "mg/kg"), "IV", "daily", (day, day + 3), "rejection"
            )
        if "IVIG" in plan.treatment:
            book.add(
                "immune globulin", (2.0, "g/kg"), "IV", "once", (day + 1, day + 1), "rejection"
            )
        if "valganciclovir" in plan.treatment:
            book.add("valganciclovir", (900.0, "mg"), "oral", "BID", (day, day + 20), "infection")


def make_med_orders(patient: Patient, course: Course, key: str, rng: Rng) -> list[MedOrder]:
    """All medication orders for one patient, in start-date order."""
    ended = course.end_day < course.follow_up_days
    book = OrderBook(patient, key, course.end_day if ended else None)
    end = course.end_day
    add_stepped(book, "tacrolimus", tacrolimus_steps(rng), "BID", end)
    add_stepped(book, "mycophenolate mofetil", mmf_steps(course, rng), "BID", end)
    add_stepped(book, "prednisone", list(PREDNISONE_TAPER), "daily", end)
    add_episode_treatment(book, course)
    return sorted(book.orders, key=lambda o: (o.start_date, o.order_id))
