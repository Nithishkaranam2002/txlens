"""FHIR-like JSON bundles (one per patient): Patient, Procedure, Condition, Encounter,
MedicationRequest. Deaths are Patient.deceasedDateTime; graft loss is a dated Condition.

The three centers fill these differently: center A codes everything, center B uses free text
and its own conventions, center C is in between. That is the mess the pipeline must handle.
"""

import json
from datetime import date
from pathlib import Path
from typing import Any

from txlens_datagen.config import Center
from txlens_datagen.models import Encounter, MedOrder, PatientRecord
from txlens_datagen.notes import DRUG_NAMES
from txlens_datagen.rand import Rng, chance, pick

Json = dict[str, Any]
SNOMED = "http://snomed.info/sct"
RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
SNOMED_CODES = {
    "kidney_transplant": ("70536003", "Transplantation of kidney"),
    "heart_transplant": ("32413006", "Transplantation of heart"),
    "diabetes": ("44054006", "Diabetes mellitus type 2"),
    "hypertension": ("38341003", "Hypertensive disorder"),
    "esrd": ("46177005", "End-stage renal disease"),
    "heart_failure": ("84114007", "Heart failure"),
    "kidney_graft_failure": ("236583003", "Failed renal transplant"),
    "heart_graft_failure": ("233934000", "Cardiac transplant failure"),
}
ENCOUNTER_CLASS = {"clinic": "AMB", "biopsy": "AMB", "admission": "IMP"}


def codeable(concept: str, text: str, coded: bool) -> Json:
    """A CodeableConcept with SNOMED coding (when `coded`) and free text."""
    if not coded or concept not in SNOMED_CODES:
        return {"text": text}
    code, display = SNOMED_CODES[concept]
    return {"coding": [{"system": SNOMED, "code": code, "display": display}], "text": text}


def is_coded(center: Center, rng: Rng) -> bool:
    """Center A always codes, B never, C about half the time."""
    return center.code == "a" or (center.code == "c" and chance(rng, 0.5))


def fhir_date(day: date, center: Center) -> str:
    """A date as the center writes it: center B always adds a midnight time part."""
    return f"{day.isoformat()}T00:00:00" if center.code == "b" else day.isoformat()


def patient_resource(record: PatientRecord, center: Center) -> Json:
    """Demographics and death. Center B writes gender as M/F and dates with a time part."""
    p = record.patient
    gender = p.sex if center.code == "b" else {"F": "female", "M": "male"}[p.sex]
    resource: Json = {
        "resourceType": "Patient",
        "id": p.patient_id,
        "identifier": [{"system": f"urn:txlens:{center.tenant_id}:mrn", "value": p.mrn}],
        "name": [{"family": p.last_name, "given": [p.first_name]}],
        "gender": gender,
        "birthDate": fhir_date(p.birth_date, center),
        "managingOrganization": {"display": center.name},
    }
    if record.outcome.death_date is not None:
        resource["deceasedDateTime"] = fhir_date(record.outcome.death_date, center)
    return resource


def procedure_resource(record: PatientRecord, center: Center, rng: Rng) -> Json:
    """The transplant itself, with donor type as an extension."""
    p = record.patient
    concept = f"{p.organ}_transplant"
    text = f"{p.organ.capitalize()} transplant - {p.donor_type} donor"
    return {
        "resourceType": "Procedure",
        "id": f"{p.patient_id}-tx",
        "status": "completed",
        "subject": {"reference": f"Patient/{p.patient_id}"},
        "code": codeable(concept, text, is_coded(center, rng)),
        "performedDateTime": p.transplant_date.isoformat(),
        "extension": [{"url": "urn:txlens:donor-type", "valueString": p.donor_type}],
    }


def condition_resources(record: PatientRecord, center: Center, rng: Rng) -> list[Json]:
    """Problem list: primary disease and comorbidities."""
    p = record.patient
    items = [("esrd" if p.organ == "kidney" else "heart_failure", p.primary_disease)]
    if p.diabetes:
        items.append(("diabetes", "Type 2 diabetes"))
    if p.hypertension:
        items.append(("hypertension", "Hypertension"))
    if p.prior_transplant:
        items.append(("prior_transplant", f"History of prior {p.organ} transplant"))
    return [
        {
            "resourceType": "Condition",
            "id": f"{p.patient_id}-c{n}",
            "subject": {"reference": f"Patient/{p.patient_id}"},
            "code": codeable(concept, text, is_coded(center, rng)),
        }
        for n, (concept, text) in enumerate(items, start=1)
    ]


def graft_loss_resource(record: PatientRecord, center: Center, rng: Rng) -> Json | None:
    """Graft failure as a dated Condition, or None when the graft survived follow-up."""
    p, outcome = record.patient, record.outcome
    if outcome.graft_loss_date is None:
        return None
    text = f"{p.organ.capitalize()} graft failure - {outcome.graft_loss_cause}"
    return {
        "resourceType": "Condition",
        "id": f"{p.patient_id}-graft-loss",
        "subject": {"reference": f"Patient/{p.patient_id}"},
        "code": codeable(f"{p.organ}_graft_failure", text, is_coded(center, rng)),
        "onsetDateTime": fhir_date(outcome.graft_loss_date, center),
    }


def encounter_resource(record: PatientRecord, enc: Encounter) -> Json:
    """One encounter with its class and period."""
    return {
        "resourceType": "Encounter",
        "id": enc.encounter_id,
        "status": "finished",
        "class": {"code": ENCOUNTER_CLASS[enc.kind]},
        "type": [{"text": enc.kind}],
        "subject": {"reference": f"Patient/{record.patient.patient_id}"},
        "period": {"start": enc.start_date.isoformat(), "end": enc.end_date.isoformat()},
    }


def dosage(order: MedOrder, name: str, center: Center) -> list[Json]:
    """Dose as structured fields (A), free text only (B) or both (C)."""
    dose = f"{order.dose_value:g}"
    text = f"{name} {dose}{order.dose_unit} {order.route} {order.frequency}".lower()
    if center.code == "b":
        return [{"text": text}]
    structured: Json = {
        "timing": {"code": {"text": order.frequency}},
        "route": {"text": order.route},
        "doseAndRate": [{"doseQuantity": {"value": order.dose_value, "unit": order.dose_unit}}],
    }
    return [structured | {"text": text}] if center.code == "c" else [structured]


def medication_resource(record: PatientRecord, order: MedOrder, center: Center, rng: Rng) -> Json:
    """One medication order. The drug name may be generic, brand or an abbreviation."""
    name = pick(rng, DRUG_NAMES[order.drug])
    concept: Json = {"text": name}
    if order.rxnorm and is_coded(center, rng):
        concept["coding"] = [{"system": RXNORM, "code": order.rxnorm, "display": order.drug}]
    period: Json = {"start": order.start_date.isoformat()}
    if order.end_date is not None:
        period["end"] = order.end_date.isoformat()
    return {
        "resourceType": "MedicationRequest",
        "id": order.order_id,
        "status": "active" if order.end_date is None else "completed",
        "intent": "order",
        "subject": {"reference": f"Patient/{record.patient.patient_id}"},
        "medicationCodeableConcept": concept,
        "authoredOn": order.start_date.isoformat(),
        "dosageInstruction": dosage(order, name, center),
        "dispenseRequest": {"validityPeriod": period},
    }


def make_bundle(record: PatientRecord, center: Center, rng: Rng) -> Json:
    """The patient's full bundle."""
    resources = [
        patient_resource(record, center),
        procedure_resource(record, center, rng),
        *condition_resources(record, center, rng),
        *(encounter_resource(record, enc) for enc in record.encounters),
        *(medication_resource(record, order, center, rng) for order in record.meds),
    ]
    graft_loss = graft_loss_resource(record, center, rng)
    if graft_loss is not None:
        resources.append(graft_loss)
    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [{"resource": r} for r in resources],
    }


def write_bundle(record: PatientRecord, center: Center, out_dir: Path, rng: Rng) -> Path:
    """Write one patient's bundle to fhir/<center>/<patient_id>.json."""
    path = out_dir / center.tenant_id / f"{record.patient.patient_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(make_bundle(record, center, rng), indent=1), encoding="utf-8")
    return path
