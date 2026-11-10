"""Raw files must agree with the ground truth once each center's format is understood."""

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest

from txlens_datagen.models import LabName
from txlens_datagen.writers.lab_feeds import DIALECTS, UMOL_PER_MG_DL

Row = dict[str, str]


def read_csv(path: Path, delimiter: str = ",") -> list[Row]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def parse_value(code: str, row: list[str], lab: str) -> float:
    """Convert one raw row's value to standard units the way a cleaner would."""
    dialect = DIALECTS[code]
    text = row[dialect.value_col].strip().rstrip("%").replace(",", ".")
    value = float(text)
    unit = row[dialect.value_col + 1]
    if lab == "creatinine" and "mol" in unit:
        return value / UMOL_PER_MG_DL
    if lab == "dd_cfdna" and unit == "fraction":
        return value * 100
    return value


DATE_FORMATS = {"a": "%Y-%m-%d %H:%M", "b": "%d/%m/%Y %H:%M", "c": "%m/%d/%Y %I:%M %p"}
TOLERANCE = {"creatinine": 0.006, "dd_cfdna": 1e-9}


@pytest.fixture(scope="module")
def truth(generated: Path) -> dict[str, list[Row]]:
    gt = generated / "ground_truth"
    names = ["labs", "lab_feed_issues", "patients", "medications", "notes", "biopsies"]
    return {name: read_csv(gt / f"{name}.csv") for name in names}


def test_every_lab_matches_its_raw_row(generated: Path, truth: dict[str, list[Row]]) -> None:
    issues: dict[tuple[str, int], set[str]] = {}
    for issue in truth["lab_feed_issues"]:
        issues.setdefault((issue["raw_file"], int(issue["raw_row"])), set()).add(issue["issue"])
    mrn = {p["patient_id"]: p["mrn"] for p in truth["patients"]}
    for code, dialect in DIALECTS.items():
        with (generated / "raw" / "labs" / dialect.file_name).open(encoding="utf-8") as handle:
            rows = list(csv.reader(handle, delimiter=dialect.delimiter))[1:]
        labs = [lab for lab in truth["labs"] if lab["raw_file"] == dialect.file_name]
        duplicates = [
            k for k, v in issues.items() if k[0] == dialect.file_name and "duplicate" in v
        ]
        assert len(rows) == len(labs) + len(duplicates)
        for _, row_number in duplicates:
            assert rows[row_number - 1] == rows[row_number - 2]
        for lab in labs:
            row = rows[int(lab["raw_row"]) - 1]
            flags = issues.get((dialect.file_name, int(lab["raw_row"])), set())
            assert row[0] == mrn[lab["patient_id"]]
            assert row[dialect.name_col] in dialect.test_names[cast(LabName, lab["lab"])]
            if not flags & {"bad_date"}:
                when = datetime.strptime(row[dialect.date_col], DATE_FORMATS[code])
                assert when == datetime.fromisoformat(lab["collected_at"])
            if not flags & {"missing_value", "censored"}:
                parsed = parse_value(code, row, lab["lab"])
                tolerance = TOLERANCE.get(lab["lab"], 0.05)
                assert abs(parsed - float(lab["value"])) <= tolerance + 1e-9


def test_center_a_has_coded_and_uncoded_labs(generated: Path) -> None:
    rows = read_csv(generated / "raw" / "labs" / "center_a_labs.csv")
    creatinine = [r for r in rows if r["LOINC"] or r["TEST_NAME"].upper().startswith("CREAT")]
    coded = [r for r in creatinine if r["LOINC"]]
    assert 0 < len(coded) < len(creatinine)


def test_lab_feeds_contain_every_kind_of_mess(truth: dict[str, list[Row]]) -> None:
    kinds = {issue["issue"] for issue in truth["lab_feed_issues"]}
    expected = {"duplicate", "bad_date", "name_variant", "whitespace", "censored"}
    assert expected <= kinds


def test_note_fact_spans_point_at_their_text(generated: Path, truth: dict[str, list[Row]]) -> None:
    raw = generated / "raw"
    texts = {n["note_id"]: (raw / n["file"]).read_text(encoding="utf-8") for n in truth["notes"]}
    lines = (generated / "ground_truth" / "note_facts.jsonl").read_text().splitlines()
    facts = [json.loads(line) for line in lines]
    assert facts
    for fact in facts:
        assert texts[fact["note_id"]][fact["start"] : fact["end"]] == fact["text"]
    assert any(f["negated"] for f in facts)
    assert {f["temporality"] for f in facts} == {"current", "historical", "hypothetical"}
    assert {f["experiencer"] for f in facts} == {"patient", "family"}


def test_fhir_medications_match_ground_truth(generated: Path, truth: dict[str, list[Row]]) -> None:
    by_order = {m["order_id"]: m for m in truth["medications"]}
    seen = set()
    for path in (generated / "raw" / "fhir").rglob("*.json"):
        bundle = json.loads(path.read_text(encoding="utf-8"))
        for entry in bundle["entry"]:
            resource = entry["resource"]
            if resource["resourceType"] != "MedicationRequest":
                continue
            order = by_order[resource["id"]]
            seen.add(resource["id"])
            assert resource["authoredOn"] == order["start_date"]
            dose = resource["dosageInstruction"][0]
            if "doseAndRate" in dose:
                assert dose["doseAndRate"][0]["doseQuantity"]["value"] == float(order["dose_value"])
            else:
                assert f"{float(order['dose_value']):g}{order['dose_unit']}" in dose["text"]
    assert seen == set(by_order)


def test_each_patient_has_one_bundle(generated: Path, truth: dict[str, list[Row]]) -> None:
    bundles = {p.stem for p in (generated / "raw" / "fhir").rglob("*.json")}
    assert bundles == {p["patient_id"] for p in truth["patients"]}


def test_only_coded_centers_send_rxnorm_codes(generated: Path) -> None:
    coded_by_center: dict[str, set[str]] = {}
    for path in (generated / "raw" / "fhir").rglob("*.json"):
        center = path.parent.name
        for entry in json.loads(path.read_text(encoding="utf-8"))["entry"]:
            resource = entry["resource"]
            if resource["resourceType"] == "MedicationRequest":
                for coding in resource["medicationCodeableConcept"].get("coding", []):
                    coded_by_center.setdefault(center, set()).add(coding["code"])
    assert "center_b" not in coded_by_center
    assert {"42316", "68149", "8640"} <= coded_by_center["center_a"]
