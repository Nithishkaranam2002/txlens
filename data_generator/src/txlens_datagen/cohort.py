"""Patient demographics and transplant facts.

Names are made up: invented first names and surnames built from invented syllables, so no
patient is a real or famous person.
"""

from datetime import date, timedelta

from txlens_datagen.config import Center, GeneratorConfig, Organ
from txlens_datagen.models import DonorType, Patient, Sex
from txlens_datagen.rand import Rng, chance, make_uuid, pick, pick_weighted, randint, random_date

# fmt: off
FIRST_NAMES: dict[Sex, tuple[str, ...]] = {
    "F": ("Alessa", "Brinna", "Caelia", "Delvina", "Elowyn", "Fennra", "Gwenlia", "Halvia",
          "Isolde", "Jessamyr", "Kirsa", "Lunetta", "Marivel", "Nerissa", "Orlaine", "Perrin",
          "Quilla", "Rosamund", "Saffira", "Talwen", "Ulvina", "Velora", "Wrenna", "Ysolde"),
    "M": ("Alder", "Brannoc", "Corvin", "Daxtor", "Edrick", "Fennick", "Garrow", "Halden",
          "Ivor", "Jasper", "Kellan", "Lorcan", "Merrick", "Nolric", "Osric", "Perran",
          "Quentin", "Rowan", "Silas", "Tobren", "Ulric", "Varek", "Wystan", "Yorick"),
}
SURNAME_STARTS = ("Ash", "Bram", "Cor", "Dun", "Elm", "Fen", "Gal", "Hollin", "Ivy", "Kes",
                  "Lorn", "Mar", "Nor", "Oak", "Pell", "Quen", "Rav", "Sel", "Thorn", "Vell",
                  "Wyn", "Yarr")
SURNAME_ENDS = ("brook", "dale", "wick", "mere", "holt", "ridge", "vane", "worth", "croft",
                "ley", "shaw", "thorpe", "by", "gate", "moor")
# fmt: on
PRIMARY_DISEASES: dict[Organ, tuple[str, ...]] = {
    "kidney": (
        "diabetic nephropathy",
        "hypertensive nephrosclerosis",
        "IgA nephropathy",
        "polycystic kidney disease",
        "focal segmental glomerulosclerosis",
        "lupus nephritis",
    ),
    "heart": ("ischemic cardiomyopathy", "dilated cardiomyopathy", "hypertrophic cardiomyopathy"),
}


def make_mrn(center: Center, index: int) -> str:
    """Center-specific MRN format (each hospital numbers patients its own way)."""
    if center.code == "a":
        return f"A{1_000_000 + index:07d}"
    if center.code == "b":
        return f"MRN-B-{index:06d}"
    return f"{730_000_000 + index * 7:09d}"


def make_surname(rng: Rng) -> str:
    """A made-up surname from invented syllables."""
    return pick(rng, SURNAME_STARTS) + pick(rng, SURNAME_ENDS)


def make_patient(index: int, config: GeneratorConfig, rng: Rng) -> Patient:
    """Draw one patient's demographics and transplant facts."""
    center = pick_weighted(rng, {c: c.weight for c in config.centers})
    sex: Sex = "F" if chance(rng, 0.4) else "M"
    organ: Organ = "kidney" if chance(rng, config.kidney_fraction) else "heart"
    donor: DonorType = "deceased" if chance(rng, config.deceased_donor_fraction) else "living"
    if organ == "heart":
        donor = "deceased"
    transplant_date = random_date(rng, *config.transplant_window)
    age = randint(rng, *config.age_range)
    birth_date = transplant_date - timedelta(days=age * 365 + randint(rng, 0, 364))
    diabetes = chance(rng, config.diabetes_rate)
    disease = pick(rng, PRIMARY_DISEASES[organ])
    if organ == "kidney" and diabetes and chance(rng, 0.6):
        disease = "diabetic nephropathy"
    dgf_rate = config.dgf_rate_deceased if donor == "deceased" else config.dgf_rate_living
    return Patient(
        patient_id=make_uuid(rng),
        tenant_id=center.tenant_id,
        mrn=make_mrn(center, index),
        first_name=pick(rng, FIRST_NAMES[sex]),
        last_name=make_surname(rng),
        sex=sex,
        birth_date=birth_date,
        organ=organ,
        donor_type=donor,
        transplant_date=transplant_date,
        primary_disease=disease,
        diabetes=diabetes or disease == "diabetic nephropathy",
        hypertension=chance(rng, config.hypertension_rate),
        prior_transplant=chance(rng, config.prior_transplant_rate),
        dgf=organ == "kidney" and chance(rng, dgf_rate),
    )


def age_on(patient: Patient, day: date) -> int:
    """Age in whole years on a given date."""
    years = day.year - patient.birth_date.year
    before_birthday = (day.month, day.day) < (patient.birth_date.month, patient.birth_date.day)
    return years - int(before_birthday)


def on_day(patient: Patient, day: int) -> date:
    """Calendar date of a day counted from the transplant (day 0)."""
    return patient.transplant_date + timedelta(days=day)
