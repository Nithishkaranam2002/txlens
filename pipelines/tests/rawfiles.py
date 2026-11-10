"""Tiny hand-written raw files in each center's format, for transform tests."""

import json
from collections.abc import Mapping
from pathlib import Path

LAB_A = (
    "MRN,TEST_NAME,LOINC,RESULT,UNITS,COLLECTED_DT\r\n"
    "A1,CREATININE,2160-0,1.20,mg/dL,2024-08-09 07:22\r\n"
    'A1,"Creatinine, serum",,  1.30 ,mg/dL,2024-08-10 07:22\r\n'
    "A1,DD-CFDNA,,0.23%,,N/A\r\n"
    "A1,DD-CFDNA,,0.23%,,N/A\r\n"
    "A9,eGFR,,,mL/min/1.73m2,2024-08-09 07:22\r\n"
)
LAB_B = (
    "patient_id;analyte;value;unit;collection_date\r\n"
    "MRN-B-1;Creatinine (enzymatic);159;µmol/L;04/09/2023 09:22\r\n"
    "MRN-B-1;eGFR;>90;mL/min/1.73m²;31/02/2024 08:00\r\n"
    "MRN-B-1;dd-cfDNA fraction;0,0019;fraction;04/09/2023 09:22\r\n"
)
LAB_C = (
    "PatientMRN,LabCode,LabDescription,ResultValue,ResultUnit,DrawnAt\r\n"
    "730000063,CRE,Creatinine,139,umol/L,6/9/2022 7:31 AM\r\n"
    "730000063,DDCF,dd-cfDNA assay,<0.05,%,6/9/2022 7:31 PM\r\n"
    "730000063,XYZ,Mystery test,5,mg/dL,6/9/2022 7:31 AM\r\n"
    "730000063,TACT,Tacrolimus Trough,8.1,furlongs,6/9/2022 7:31 AM\r\n"
)


def patient(pid: str, tenant: str, mrn: str, **extra: object) -> dict[str, object]:
    return {
        "resourceType": "Patient",
        "id": pid,
        "identifier": [{"system": f"urn:txlens:{tenant}:mrn", "value": mrn}],
        "gender": "female",
        "birthDate": "1970-01-02",
        **extra,
    }


def bundle(*resources: Mapping[str, object]) -> str:
    return json.dumps({"resourceType": "Bundle", "entry": [{"resource": r} for r in resources]})


def write_raw(root: Path) -> Path:
    """A raw folder like data/raw with a few rows per source and center."""
    for name, text in (("center_a", LAB_A), ("center_b", LAB_B), ("center_c", LAB_C)):
        (root / "labs").mkdir(parents=True, exist_ok=True)
        (root / "labs" / f"{name}_labs.csv").write_bytes(text.encode("utf-8"))
    for tenant, mrn in (("center_a", "A1"), ("center_b", "MRN-B-1"), ("center_c", "730000063")):
        folder = root / "fhir" / tenant
        folder.mkdir(parents=True, exist_ok=True)
        tx = {
            "resourceType": "Procedure",
            "id": f"p-{tenant}-tx",
            "performedDateTime": "2024-01-05",
        }
        (folder / f"p-{tenant}.json").write_text(bundle(patient(f"p-{tenant}", tenant, mrn), tx))
        notes = root / "notes" / tenant
        notes.mkdir(parents=True, exist_ok=True)
        (notes / "N-000001-001.txt").write_text(
            f"Clinic note\nMRN: {mrn}\nDate of service: 2024-02-01\n"
        )
        pdfs = root / "pathology" / tenant
        pdfs.mkdir(parents=True, exist_ok=True)
        (pdfs / "BX-000001-01.pdf").write_bytes(b"%PDF-1.4 fake")
    return root
