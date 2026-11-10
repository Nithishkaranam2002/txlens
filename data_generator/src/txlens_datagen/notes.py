"""Free-text clinic and biopsy-result notes, written the way clinicians write.

Notes use abbreviations (Cr, tacro, Tx, DGF), negations ("no evidence of rejection"), past
history, family history and hypothetical plans. Every fact is recorded with its span.
"""

from datetime import date, timedelta

from txlens_datagen.cohort import age_on
from txlens_datagen.config import GeneratorConfig
from txlens_datagen.models import (
    Biopsy,
    Encounter,
    Experiencer,
    InjuryEpisode,
    LabName,
    LabResult,
    MedOrder,
    Note,
    PatientRecord,
    RejectionEpisode,
    Temporality,
)
from txlens_datagen.note_builder import Mention, NoteBuilder
from txlens_datagen.rand import Rng, chance, pick

DRUG_NAMES = {
    "tacrolimus": ("tacrolimus", "tacro", "Tac", "FK506", "Prograf"),
    "mycophenolate mofetil": ("mycophenolate mofetil", "MMF", "CellCept", "mycophenolate"),
    "prednisone": ("prednisone", "pred", "PDN"),
    "methylprednisolone": ("methylprednisolone", "Solu-Medrol", "methylpred"),
    "antithymocyte globulin": ("Thymoglobulin", "rATG", "ATG"),
    "immune globulin": ("IVIG", "IV immune globulin"),
    "valganciclovir": ("valganciclovir", "Valcyte", "valgan"),
}
LAB_NAMES: dict[LabName, tuple[str, ...]] = {
    "creatinine": ("Cr", "creat", "creatinine", "SCr"),
    "egfr": ("eGFR", "GFR"),
    "tacrolimus_trough": ("tacro trough", "FK level", "tac level", "tacrolimus trough"),
    "dd_cfdna": ("dd-cfDNA", "donor-derived cfDNA", "cfDNA"),
    "dsa_mfi": ("DSA",),
}
CONDITION_NAMES = {
    "diabetes": ("DM2", "T2DM", "type 2 diabetes"),
    "hypertension": ("HTN", "hypertension"),
    "esrd": ("ESRD", "end-stage renal disease"),
    "heart_failure": ("end-stage heart failure", "advanced heart failure"),
    "coronary_artery_disease": ("CAD", "coronary artery disease"),
    "polycystic_kidney_disease": ("PKD", "polycystic kidney disease"),
    "cmv_infection": ("CMV viremia", "CMV infection"),
    "bk_nephropathy": ("BK nephropathy", "BKVAN", "polyomavirus nephropathy"),
    "acute_tubular_injury": ("ATN", "acute tubular injury"),
    "dgf": ("DGF", "delayed graft function"),
    "rejection": ("rejection", "acute rejection"),
    "fever": ("fever", "fevers"),
    "dysuria": ("dysuria",),
    "edema": ("edema", "LE edema"),
    "dyspnea": ("SOB", "dyspnea"),
    "graft_tenderness": ("graft tenderness",),
}
REJECTION_CONCEPT = {
    "borderline": "borderline_rejection",
    "TCMR": "tcmr",
    "ABMR": "abmr",
    "mixed": "mixed_rejection",
}
REJECTION_NAMES = {
    ("kidney", "borderline"): ("borderline changes", "borderline rejection"),
    ("kidney", "TCMR"): ("TCMR", "acute T-cell mediated rejection"),
    ("kidney", "ABMR"): ("ABMR", "antibody-mediated rejection"),
    ("kidney", "mixed"): ("mixed rejection", "mixed TCMR/ABMR"),
    ("heart", "borderline"): ("mild ACR", "mild acute cellular rejection"),
    ("heart", "TCMR"): ("ACR", "acute cellular rejection"),
    ("heart", "ABMR"): ("AMR", "antibody-mediated rejection"),
    ("heart", "mixed"): ("mixed ACR/AMR", "mixed rejection"),
}
SYMPTOMS = ("fever", "dysuria", "edema", "dyspnea", "graft_tenderness")


def condition(
    rng: Rng,
    concept: str,
    *,
    negated: bool = False,
    temporality: Temporality = "current",
    experiencer: Experiencer = "patient",
) -> Mention:
    """A condition or symptom mention with a random surface form."""
    surface = pick(rng, CONDITION_NAMES[concept])
    return Mention(surface, concept, "condition", negated, temporality, experiencer)


def drug(rng: Rng, name: str, order: MedOrder | None = None) -> Mention:
    """A drug mention, carrying the dose when an order is given."""
    surface = pick(rng, DRUG_NAMES[name])
    if order is None:
        return Mention(surface, name, "medication")
    return Mention(surface, name, "medication", value=order.dose_value, unit=order.dose_unit)


def lab(rng: Rng, result: LabResult) -> Mention:
    """A lab-name mention carrying the measured value."""
    return Mention(
        pick(rng, LAB_NAMES[result.lab]), result.lab, "lab", value=result.value, unit=result.unit
    )


def fmt_num(value: float) -> str:
    """3.0 -> '3', 7.5 -> '7.5'."""
    return f"{value:g}"


def fmt_date(rng: Rng, day: date) -> str:
    """A date written in one of the styles clinicians use."""
    return pick(rng, (day.isoformat(), day.strftime("%m/%d/%Y"), f"{day.month}/{day.day}/{day:%y}"))


def latest(labs: list[LabResult], name: LabName, on: date, window_days: int) -> LabResult | None:
    """Most recent result of a lab on or before `on`, within the window."""
    found = None
    for result in labs:
        day = result.collected_at.date()
        if result.lab == name and on - timedelta(days=window_days) <= day <= on:
            found = result
    return found


def header(b: NoteBuilder, record: PatientRecord, center: str, title: str, on: date) -> None:
    """Note title and patient identifiers (these are PHI the de-identification step removes)."""
    p = record.patient
    b.line(title)
    b.line(center)
    b.line(
        f"Patient: {p.last_name}, {p.first_name}    MRN: {p.mrn}    DOB: {p.birth_date:%m/%d/%Y}"
    )
    b.line(f"Date of service: {on.isoformat()}")
    b.line()


def transplant_mention(rng: Rng, record: PatientRecord) -> Mention:
    """The transplant procedure itself (historical)."""
    p = record.patient
    if p.organ == "heart":
        names: tuple[str, ...] = ("OHT", "heart Tx", "orthotopic heart transplant")
    elif p.donor_type == "living":
        names = ("LDKT", "living donor KTx", "living donor kidney Tx")
    else:
        names = ("DDKT", "deceased donor KTx", "deceased donor kidney Tx")
    return Mention(pick(rng, names), f"{p.organ}_transplant", "procedure", temporality="historical")


def hpi(b: NoteBuilder, record: PatientRecord, on: date, rng: Rng) -> None:
    """History of present illness, including early course and recent episodes."""
    p = record.patient
    months = (on - p.transplant_date).days // 30
    b.add(
        f"HPI: {age_on(p, on)} y/o {p.sex} s/p {{tx}} on {fmt_date(rng, p.transplant_date)} "
        f"({months} mo post-Tx), here for routine follow-up. ",
        tx=transplant_mention(rng, record),
    )
    if p.organ == "kidney" and p.dgf:
        b.add(
            "Post-op course c/b {dgf} requiring HD x2. ",
            dgf=condition(rng, "dgf", temporality="historical"),
        )
    elif p.organ == "kidney":
        b.add(
            "Immediate graft function without {dgf}. ",
            dgf=condition(rng, "dgf", negated=True, temporality="historical"),
        )
    recent = [r for r in record.rejections if timedelta(0) < on - r.biopsy_date <= timedelta(180)]
    for episode in recent:
        b.add(
            f"Bx {fmt_date(rng, episode.biopsy_date)} showed {{rej}}, treated. ",
            rej=rejection_mention(rng, record, episode, temporality="historical"),
        )
    symptoms = [s for s in SYMPTOMS if chance(rng, 0.35)] or ["fever"]
    b.add("Denies ")
    for i, symptom in enumerate(symptoms):
        b.add(", " if i else "")
        b.mention(condition(rng, symptom, negated=True))
    b.line(".")
    b.line()


def rejection_mention(
    rng: Rng, record: PatientRecord, episode: RejectionEpisode, temporality: Temporality
) -> Mention:
    """Mention of a specific rejection type."""
    names = REJECTION_NAMES[(record.patient.organ, episode.rejection_type)]
    concept = REJECTION_CONCEPT[episode.rejection_type]
    return Mention(pick(rng, names), concept, "condition", temporality=temporality)


def history(b: NoteBuilder, record: PatientRecord, rng: Rng) -> None:
    """Past medical history and family history."""
    p = record.patient
    b.line("PMH:")
    if p.organ == "kidney":
        b.line(
            f"- {{esrd}} 2/2 {p.primary_disease}",
            esrd=condition(rng, "esrd", temporality="historical"),
        )
    else:
        b.line(
            f"- {{hf}} 2/2 {p.primary_disease}",
            hf=condition(rng, "heart_failure", temporality="historical"),
        )
    if p.diabetes:
        b.line("- {dm}", dm=condition(rng, "diabetes"))
    if p.hypertension:
        b.line("- {htn}", htn=condition(rng, "hypertension"))
    if p.prior_transplant:
        b.line(
            "- prior {tx}, failed",
            tx=Mention(
                pick(rng, ("Tx", "transplant")),
                "prior_transplant",
                "procedure",
                temporality="historical",
            ),
        )
    if chance(rng, 0.1):
        b.line(
            "- h/o {cmv}, resolved", cmv=condition(rng, "cmv_infection", temporality="historical")
        )
    b.add("FHx: ")
    relatives = [
        ("Mother", "diabetes"),
        ("Father", "coronary_artery_disease"),
        ("Sister", "hypertension"),
    ]
    if p.primary_disease == "polycystic kidney disease":
        relatives.append(("Brother", "polycystic_kidney_disease"))
    chosen = [r for r in relatives if chance(rng, 0.3)]
    if not chosen:
        b.write("noncontributory.")
    for i, (who, concept) in enumerate(chosen):
        b.add(
            f"{'; ' if i else ''}{who} with {{c}}", c=condition(rng, concept, experiencer="family")
        )
    b.line()
    b.line()


def active_orders(record: PatientRecord, on: date) -> list[MedOrder]:
    """Orders active on a date."""
    return [
        o for o in record.meds if o.start_date <= on and (o.end_date is None or o.end_date >= on)
    ]


def meds_section(b: NoteBuilder, record: PatientRecord, on: date, rng: Rng) -> None:
    """Current medications with doses written in mixed styles."""
    b.line("Meds:")
    for order in active_orders(record, on):
        freq = order.frequency if order.frequency != "BID" else pick(rng, ("BID", "bid", "q12h"))
        space = pick(rng, (" ", ""))
        route = "PO " if order.route == "oral" and chance(rng, 0.5) else ""
        b.line(
            f"- {{d}} {fmt_num(order.dose_value)}{space}{order.dose_unit} {route}{freq}",
            d=drug(rng, order.drug, order),
        )
    b.line()


def labs_section(b: NoteBuilder, record: PatientRecord, on: date, rng: Rng) -> None:
    """Most recent labs, quoted with the same values as the lab feed."""
    b.line("Labs:")
    for name in ("creatinine", "egfr", "tacrolimus_trough", "dd_cfdna"):
        result = latest(record.labs, name, on, 45)
        if result is not None:
            unit = "%" if name == "dd_cfdna" else ""
            b.line(
                f"- {{l}} {fmt_num(result.value)}{unit} ({result.collected_at:%m/%d})",
                l=lab(rng, result),
            )
    dsa = latest(record.labs, "dsa_mfi", on, 120)
    if dsa is not None and dsa.value >= 1000:
        b.line(f"- {{d}} positive, MFI {fmt_num(dsa.value)}", d=lab(rng, dsa))
    elif dsa is not None:
        b.line("- No {d} detected", d=Mention("DSA", "dsa", "lab", negated=True))
    b.line()


def plan_section(
    b: NoteBuilder, record: PatientRecord, on: date, config: GeneratorConfig, rng: Rng
) -> None:
    """Assessment and plan, with negations and hypothetical statements."""
    p = record.patient
    day = (on - p.transplant_date).days
    biopsy = Mention("biopsy", "graft_biopsy", "procedure", temporality="hypothetical")
    rising = any(e.signal_onset_date <= on < e.biopsy_date for e in record.rejections) or any(
        e.onset_date <= on < e.biopsy_date for e in record.injuries
    )
    b.line("A/P:")
    if rising:
        b.line(
            "Graft function worse than baseline with rising markers. Will arrange for-cause {bx}.",
            bx=biopsy,
        )
    else:
        b.line(
            "Stable graft function. No evidence of {rej}.",
            rej=condition(rng, "rejection", negated=True),
        )
        threshold = config.organs[p.organ].dd_cfdna.thresholds_pct[-1]
        b.line(
            f"If dd-cfDNA rises above {fmt_num(threshold)}%, will plan for-cause {{bx}}.", bx=biopsy
        )
    b.line(
        "Reviewed signs of {rej} to watch for.",
        rej=condition(rng, "rejection", temporality="hypothetical"),
    )
    follow_up = pick(rng, ("4 wks", "1 mo", "3 mo"))
    b.line(f"Continue current immunosuppression. RTC {follow_up} (POD {day}).")


def clinic_note(
    record: PatientRecord, enc: Encounter, config: GeneratorConfig, note_id: str, rng: Rng
) -> Note:
    """A routine transplant clinic note."""
    b = NoteBuilder(note_id)
    on = enc.start_date
    header(b, record, center_name(record, config), "TRANSPLANT CLINIC NOTE", on)
    hpi(b, record, on, rng)
    history(b, record, rng)
    meds_section(b, record, on, rng)
    labs_section(b, record, on, rng)
    plan_section(b, record, on, config, rng)
    return b.build(record.patient.patient_id, enc.encounter_id, on, "clinic")


def center_name(record: PatientRecord, config: GeneratorConfig) -> str:
    """Display name of the patient's transplant center."""
    return next(c.name for c in config.centers if c.tenant_id == record.patient.tenant_id)


def biopsy_result_line(
    b: NoteBuilder,
    record: PatientRecord,
    biopsy: Biopsy,
    episode: RejectionEpisode | InjuryEpisode,
    rng: Rng,
) -> None:
    """The pathology result as the clinician summarizes it."""
    if isinstance(episode, InjuryEpisode):
        b.line(
            "Result: no evidence of {rej}. Findings c/w {injury}.",
            rej=condition(rng, "rejection", negated=True),
            injury=condition(rng, episode.cause),
        )
    elif biopsy.organ == "kidney":
        grade = f", Banff grade {biopsy.banff_tcmr_grade}" if biopsy.banff_tcmr_grade else ""
        b.line(f"Result: {{rej}}{grade}.", rej=rejection_mention(rng, record, episode, "current"))
    else:
        b.line(
            f"Result: {{rej}}, ISHLT {biopsy.ishlt_acr_grade}, {biopsy.pamr_grade}.",
            rej=rejection_mention(rng, record, episode, "current"),
        )


def treatment_lines(b: NoteBuilder, record: PatientRecord, biopsy: Biopsy, rng: Rng) -> None:
    """Treatment orders started for this biopsy, written as plan lines."""
    window = (biopsy.biopsy_date, biopsy.biopsy_date + timedelta(days=1))
    started = [o for o in record.meds if window[0] <= o.start_date <= window[1]]
    for order in started:
        dose = f"{fmt_num(order.dose_value)} {order.dose_unit} {order.route} {order.frequency}"
        verb = "Reduce" if order.reason == "maintenance" else "Start"
        joiner = "to" if verb == "Reduce" else ""
        b.line(f"- {verb} {{d}} {joiner} {dose}".replace("  ", " "), d=drug(rng, order.drug, order))
    if not started:
        b.line("- Supportive care, avoid nephrotoxins, trend Cr.")


def biopsy_note(
    record: PatientRecord,
    biopsy: Biopsy,
    enc: Encounter,
    config: GeneratorConfig,
    note_id: str,
    rng: Rng,
) -> Note:
    """Note written the day after a for-cause biopsy, with result and treatment plan."""
    episodes: list[RejectionEpisode | InjuryEpisode] = [*record.rejections, *record.injuries]
    episode = next(e for e in episodes if e.biopsy_id == biopsy.biopsy_id)
    on = biopsy.biopsy_date + timedelta(days=1)
    b = NoteBuilder(note_id)
    title = "KIDNEY" if biopsy.organ == "kidney" else "HEART"
    header(b, record, center_name(record, config), f"{title} TRANSPLANT - BIOPSY RESULT NOTE", on)
    b.add("Indication: for-cause biopsy for ")
    reasons = [latest(record.labs, n, biopsy.biopsy_date, 7) for n in ("creatinine", "dd_cfdna")]
    for i, result in enumerate(r for r in reasons if r is not None):
        unit = "%" if result.lab == "dd_cfdna" else ""
        b.add(f"{' and ' if i else ''}{{l}} {fmt_num(result.value)}{unit}", l=lab(rng, result))
    b.line(".")
    b.line(f"Biopsy date: {fmt_date(rng, biopsy.biopsy_date)}")
    biopsy_result_line(b, record, biopsy, episode, rng)
    b.line("Plan:")
    treatment_lines(b, record, biopsy, rng)
    b.line("- Repeat {l} in 4 wks.", l=Mention(pick(rng, LAB_NAMES["dd_cfdna"]), "dd_cfdna", "lab"))
    return b.build(record.patient.patient_id, enc.encounter_id, on, "biopsy_result")


def make_notes(record: PatientRecord, config: GeneratorConfig, key: str, rng: Rng) -> list[Note]:
    """Clinic notes for each clinic visit and a result note for each for-cause biopsy."""
    jobs: list[tuple[date, Encounter, Biopsy | None]] = []
    for enc in record.encounters:
        if enc.kind == "clinic":
            jobs.append((enc.start_date, enc, None))
    for bx in record.biopsies:
        if bx.indication == "for_cause":
            enc = next(
                e
                for e in record.encounters
                if e.kind == "biopsy" and e.start_date == bx.biopsy_date
            )
            jobs.append((bx.biopsy_date, enc, bx))
    jobs.sort(key=lambda job: (job[0], job[1].encounter_id))
    notes = []
    for n, (_, enc, biopsy) in enumerate(jobs, start=1):
        note_id = f"N-{key}-{n:03d}"
        if biopsy is None:
            notes.append(clinic_note(record, enc, config, note_id, rng))
        else:
            notes.append(biopsy_note(record, biopsy, enc, config, note_id, rng))
    return notes
