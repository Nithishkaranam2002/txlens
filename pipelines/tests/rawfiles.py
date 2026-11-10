"""Tiny hand-written raw files in each center's format, for transform tests.

Each line is chosen to exercise one rule; the comments say which.
"""

import json
from collections.abc import Mapping
from pathlib import Path

SNOMED = "http://snomed.info/sct"
RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"

LAB_A = (
    "MRN,TEST_NAME,LOINC,RESULT,UNITS,COLLECTED_DT\r\n"
    "A1,CREATININE,2160-0,1.20,mg/dL,2024-08-09 07:22\r\n"  # 1 coded LOINC
    'A1,"Creatinine, serum",,  1.30 ,mg/dL,2024-08-10 07:22\r\n'  # 2 quoted name, whitespace
    "A1,DD-CFDNA,,0.23%,,N/A\r\n"  # 3 bad date
    "A1,DD-CFDNA,,0.23%,,N/A\r\n"  # 4 exact duplicate of 3
    "A9,eGFR,,,mL/min/1.73m2,2024-08-09 07:22\r\n"  # 5 missing value
    "A1,DD-CFDNA,,0.31%,,2024-08-11 07:00\r\n"  # 6 '%' inside the value
)
LAB_B = (
    "patient_id;analyte;value;unit;collection_date\r\n"
    "MRN-B-1;Creatinine (enzymatic);159;µmol/L;04/09/2023 09:22\r\n"  # 1 umol/L -> mg/dL
    "MRN-B-1;eGFR;>90;mL/min/1.73m²;31/02/2024 08:00\r\n"  # 2 impossible date
    "MRN-B-1;dd-cfDNA fraction;0,0019;fraction;04/09/2023 09:22\r\n"  # 3 comma, fraction
    "MRN-B-1;eGFR;>90;mL/min/1.73m²;05/09/2023 09:22\r\n"  # 4 censored
)
LAB_C = (
    "PatientMRN,LabCode,LabDescription,ResultValue,ResultUnit,DrawnAt\r\n"
    "730000063,CRE,Creatinine,139,umol/L,6/9/2022 7:31 AM\r\n"  # 1 local code
    "730000063,DDCF,dd-cfDNA assay,<0.05,%,6/9/2022 7:31 PM\r\n"  # 2 censored, PM
    "730000063,XYZ,Mystery test,5,mg/dL,6/9/2022 7:31 AM\r\n"  # 3 unmapped test
    "730000063,TACT,Tacrolimus Trough,8.1,furlongs,6/9/2022 7:31 AM\r\n"  # 4 unknown unit
    "999,CRE,Creatinine,1.0,mg/dL,6/9/2022 7:31 AM\r\n"  # 5 unknown patient
    "730000063,DSAMFI,Donor Specific Ab MFI,1200,MFI,6/10/2022 7:31 AM\r\n"  # 6 local concept
)


def coded(system: str, code: str | None, text: str) -> dict[str, object]:
    """A CodeableConcept, with a coding only when `code` is given."""
    concept: dict[str, object] = {"text": text}
    if code:
        concept["coding"] = [{"system": system, "code": code}]
    return concept


def subject(pid: str) -> dict[str, str]:
    return {"reference": f"Patient/{pid}"}


def med(
    pid: str, order: str, text: str, code: str | None, start: str, sig: str | None = None
) -> dict[str, object]:
    """A MedicationRequest; structured dose unless a free-text `sig` is given."""
    dosage: dict[str, object] = (
        {"text": sig}
        if sig
        else {
            "timing": {"code": {"text": "bid"}},
            "route": {"text": "oral"},
            "doseAndRate": [{"doseQuantity": {"value": 3.0, "unit": "mg"}}],
        }
    )
    return {
        "resourceType": "MedicationRequest",
        "id": order,
        "status": "completed",
        "subject": subject(pid),
        "medicationCodeableConcept": coded(RXNORM, code, text),
        "authoredOn": start,
        "dosageInstruction": [dosage],
        "dispenseRequest": {"validityPeriod": {"start": start, "end": "2024-02-05"}},
    }


def encounter(pid: str, eid: str, kind: str, day: str) -> dict[str, object]:
    cls = "IMP" if kind == "admission" else "AMB"
    return {
        "resourceType": "Encounter",
        "id": eid,
        "class": {"code": cls},
        "type": [{"text": kind}],
        "subject": subject(pid),
        "period": {"start": day, "end": day},
    }


def bundle(*resources: Mapping[str, object]) -> str:
    return json.dumps({"resourceType": "Bundle", "entry": [{"resource": r} for r in resources]})


BUNDLES: dict[str, list[dict[str, object]]] = {
    "center_a": [
        {
            "resourceType": "Patient",
            "id": "pa",
            "identifier": [{"system": "urn:txlens:center_a:mrn", "value": "A1"}],
            "name": [{"family": "Quorrin", "given": ["Ysolde"]}],
            "gender": "female",
            "birthDate": "1970-01-02",
            "deceasedDateTime": "2025-03-01",
        },
        {
            "resourceType": "Procedure",
            "id": "pa-tx",
            "subject": subject("pa"),
            "code": coded(SNOMED, "70536003", "Kidney transplant - deceased donor"),
            "performedDateTime": "2024-01-05",
            "extension": [{"url": "urn:txlens:donor-type", "valueString": "deceased"}],
        },
        {"resourceType": "Condition", "id": "pa-c1", "subject": subject("pa"),
         "code": coded(SNOMED, "46177005", "diabetic nephropathy")},
        {"resourceType": "Condition", "id": "pa-graft-loss", "subject": subject("pa"),
         "code": coded(SNOMED, "236583003", "Kidney graft failure - rejection (ABMR)"),
         "onsetDateTime": "2025-01-01"},
        encounter("pa", "E-A-1", "clinic", "2024-02-01"),
        encounter("pa", "E-A-2", "biopsy", "2024-03-01"),
        med("pa", "RX-A-1", "tacro", "42316", "2024-01-05"),
    ],
    "center_b": [
        {
            "resourceType": "Patient",
            "id": "pb",
            "identifier": [{"system": "urn:txlens:center_b:mrn", "value": "MRN-B-1"}],
            "name": [{"family": " Brennick ", "given": []}],
            "gender": "F",
            "birthDate": "1965-06-09T00:00:00",
        },
        {
            "resourceType": "Procedure",
            "id": "pb-tx",
            "subject": subject("pb"),
            "code": coded(SNOMED, None, "Kidney transplant - living donor"),
            "performedDateTime": "2023-08-28T00:00:00",
        },
        {"resourceType": "Condition", "id": "pb-c1", "subject": subject("pb"),
         "code": coded(SNOMED, None, "Hypertension")},
        encounter("pb", "E-B-1", "clinic", "2023-09-23"),
        med("pb", "RX-B-1", "Prograf", None, "2023-08-28", "prograf 3.5mg oral bid"),
        med("pb", "RX-B-2", "tacro", None, "2023-08-28", "tacro 3mg oral bid"),
        med("pb", "RX-B-3", "mycophenolate", None, "2023-08-28", "mycophenolate 1000mg oral bid"),
        med("pb", "RX-B-4", "tacrolimus", None, "2023-08-28", "tacrolimus 3mg oral bid"),
        med("pb", "RX-B-5", "Xyzumab", None, "2023-08-28", "xyzumab 5mg iv once"),
        med("pb", "RX-B-6", "TEST ONLY - FK-506 synonym", None, "2023-08-28",
            "test only - fk-506 synonym 2mg oral bid"),
    ],
    "center_c": [
        {
            "resourceType": "Patient",
            "id": "pc",
            "identifier": [{"system": "urn:txlens:center_c:mrn", "value": "730000063"}],
            "gender": "male",
            "birthDate": "1980-12-31",
        },
        {
            "resourceType": "Procedure",
            "id": "pc-tx",
            "subject": subject("pc"),
            "code": coded(SNOMED, "32413006", "Heart transplant - deceased donor"),
            "performedDateTime": "2022-05-01",
        },
        encounter("pc", "E-C-1", "admission", "2022-06-09"),
        med("pc", "RX-C-1", "MMF", "68149", "2022-05-01"),
        med("pnobody", "RX-C-2", "MMF", "68149", "2022-05-01"),  # unknown patient
    ],
}  # fmt: skip
MRNS = {"center_a": "A1", "center_b": "MRN-B-1", "center_c": "730000063"}
NOTE_DATES = {"center_a": "2024-02-01", "center_b": "2023-09-23", "center_c": "2022-06-10"}


def note_text(tenant: str) -> str:
    title = (
        "KIDNEY TRANSPLANT - BIOPSY RESULT NOTE"
        if tenant == "center_a"
        else ("TRANSPLANT CLINIC NOTE")
    )
    return (
        f"{title}\nSome Center\nPatient: Doe, Jane    MRN: {MRNS[tenant]}    DOB: 01/02/1970\n"
        f"Date of service: {NOTE_DATES[tenant]}\n\nHPI: stable.\n"
    )


def write_raw(root: Path) -> Path:
    """A raw folder like data/raw with a few rows per source and center."""
    (root / "labs").mkdir(parents=True, exist_ok=True)
    for name, text in (("center_a", LAB_A), ("center_b", LAB_B), ("center_c", LAB_C)):
        (root / "labs" / f"{name}_labs.csv").write_bytes(text.encode("utf-8"))
    for tenant, resources in BUNDLES.items():
        for folder in ("fhir", "notes", "pathology"):
            (root / folder / tenant).mkdir(parents=True, exist_ok=True)
        (root / "fhir" / tenant / f"{tenant}.json").write_text(bundle(*resources))
        (root / "notes" / tenant / "N-000001-001.txt").write_text(note_text(tenant))
        (root / "pathology" / tenant / "BX-000001-01.pdf").write_bytes(b"%PDF-1.4 fake")
    (root / "notes" / "center_c" / "N-000002-001.txt").write_text(
        note_text("center_c").replace("730000063", "000")  # unknown patient
    )
    return root
