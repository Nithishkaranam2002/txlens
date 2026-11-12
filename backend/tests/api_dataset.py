"""A small OMOP dataset for API tests, written straight from the generator's ground truth.

The generator runs in-process (seeded, no PDFs). Its ground truth is turned into cdm rows
plus app.patient_identity, using the real concept ids for the codes the API resolves (all
concept names are "TEST ONLY"). Notes keep their generated text, which names the patient,
so the de-identification scan has real identifiers to look for.
"""

import csv
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import Connection, text

from txlens_datagen.config import GeneratorConfig
from txlens_datagen.generate import generate

SEED = 7
N_PATIENTS = 150
ID_RANGE = 100_000_000
TENANT_ORDINAL = {"center_a": 1, "center_b": 2, "center_c": 3}
# concept_id, domain, vocabulary, class, standard, code
CONCEPTS = [
    (4322471, "Procedure", "SNOMED", "Procedure", "S", "70536003"),  # kidney transplant
    (4137127, "Procedure", "SNOMED", "Procedure", "S", "32413006"),  # heart transplant
    (4022805, "Procedure", "SNOMED", "Procedure", "S", "175901007"),  # live donor kidney tx
    (4021107, "Procedure", "SNOMED", "Procedure", "S", "175902000"),  # cadaveric kidney tx
    (1244937, "Procedure", "SNOMED", "Procedure", "S", "1293269004"),  # kidney graft biopsy
    (4308808, "Procedure", "SNOMED", "Procedure", "S", "387829002"),  # endomyocardial biopsy
    (193782, "Condition", "SNOMED", "Disorder", "S", "46177005"),  # ESRD
    (316139, "Condition", "SNOMED", "Disorder", "S", "84114007"),  # heart failure
    (201820, "Condition", "SNOMED", "Disorder", "S", "73211009"),  # diabetes mellitus
    (201826, "Condition", "SNOMED", "Disorder", "S", "44054006"),  # type 2 diabetes
    (316866, "Condition", "SNOMED", "Disorder", "S", "38341003"),  # hypertension
    (4127554, "Condition", "SNOMED", "Disorder", "S", "236583003"),  # failed renal transplant
    (4121617, "Condition", "SNOMED", "Disorder", "S", "233934000"),  # cardiac tx failure
    (4309320, "Condition", "SNOMED", "Disorder", "S", "213148006"),  # transplant rejection
    (37472871, "Condition", "SNOMED", "Disorder", "S", "1381981002"),  # ABMR
    (4138908, "Condition", "SNOMED", "Disorder", "S", "426136000"),  # delayed graft function
    (1340204, "Observation", "OMOP Extension", "Context-dependent", "S", "OMOP5165859"),
    (3016723, "Measurement", "LOINC", "Lab Test", "S", "2160-0"),  # creatinine
    (1619025, "Measurement", "LOINC", "Lab Test", "S", "98979-8"),  # eGFR
    (46236053, "Measurement", "LOINC", "Lab Test", "S", "77348-1"),  # tacrolimus trough
    (3966008, "Measurement", "LOINC", "Lab Test", "S", "103142-6"),  # dd-cfDNA
    (2000000001, "Measurement", "TxLens", "Lab Test", None, "DSA_MAX_MFI"),  # local DSA
    (950637, "Drug", "RxNorm", "Ingredient", "S", "42316"),
    (19003999, "Drug", "RxNorm", "Ingredient", "S", "68149"),
    (1551099, "Drug", "RxNorm", "Ingredient", "S", "8640"),
    (1506270, "Drug", "RxNorm", "Ingredient", "S", "6902"),
    (19117912, "Drug", "RxNorm", "Ingredient", "S", "5666"),
    (19136207, "Drug", "RxNorm", "Ingredient", "S", "1011"),
    (1703063, "Drug", "RxNorm", "Ingredient", "S", "275891"),
]
# (ancestor, descendant) besides every concept being its own ancestor.
ANCESTORS = [(4322471, 4022805), (4322471, 4021107), (201820, 201826), (4309320, 37472871)]
LAB_CONCEPTS = {"creatinine": 3016723, "egfr": 1619025, "tacrolimus_trough": 46236053,
                "dd_cfdna": 3966008}  # fmt: skip
DSA_LOCAL_CONCEPT = 2000000001
TRANSPLANT = {("kidney", "living"): 4022805, ("kidney", "deceased"): 4021107,
              ("heart", "deceased"): 4137127}  # fmt: skip
BIOPSY = {"kidney": 1244937, "heart": 4308808}
PRIMARY = {"kidney": 193782, "heart": 316139}
GRAFT_FAILURE = {"kidney": 4127554, "heart": 4121617}
# Biopsy-proven rejection as recorded conditions: ABMR (and mixed) under its own concept.
REJECTION = {"ABMR": 37472871, "mixed": 37472871, "TCMR": 4309320}
DGF, HISTORY_OF_EVENT = 4138908, 1340204
DRUGS = {code: cid for cid, domain, *_, code in CONCEPTS if domain == "Drug"}
GENDER = {"M": 8507, "F": 8532}


@dataclass(frozen=True)
class Dataset:
    """Where the ground truth is, and each ground-truth patient's (tenant, person_id)."""

    root: Path
    persons: dict[str, tuple[str, int]]

    def truth(self, name: str) -> list[dict[str, str]]:
        """Rows of one ground-truth CSV."""
        with (self.root / "ground_truth" / f"{name}.csv").open() as f:
            return list(csv.DictReader(f))


def build(out: Path) -> Dataset:
    """Generate the synthetic data (seeded) and assign tenant-ranged person ids."""
    generate(replace(GeneratorConfig(), seed=SEED, n_patients=N_PATIENTS), out, pdfs=False)
    counters = dict.fromkeys(TENANT_ORDINAL, 0)
    persons = {}
    with (out / "ground_truth" / "patients.csv").open() as f:
        for row in csv.DictReader(f):
            counters[row["tenant_id"]] += 1
            pid = TENANT_ORDINAL[row["tenant_id"]] * ID_RANGE + counters[row["tenant_id"]]
            persons[row["patient_id"]] = (row["tenant_id"], pid)
    return Dataset(out, persons)


def insert(conn: Connection, table: str, rows: list[dict[str, object]]) -> None:
    """Insert rows (all with the same keys) into one table."""
    if rows:
        columns = list(rows[0])
        names, params = ", ".join(columns), ", ".join(f":{c}" for c in columns)
        conn.execute(text(f"INSERT INTO {table} ({names}) VALUES ({params})"), rows)


def load_vocabulary(conn: Connection) -> None:
    """The test concepts and their hierarchy."""
    insert(conn, "vocab.concept", [
        {"concept_id": cid, "concept_name": f"TEST ONLY - {code}", "domain_id": domain,
         "vocabulary_id": vocab, "concept_class_id": cls, "standard_concept": std,
         "concept_code": code, "valid_start_date": date(1970, 1, 1),
         "valid_end_date": date(2099, 12, 31)}
        for cid, domain, vocab, cls, std, code in CONCEPTS
    ])  # fmt: skip
    pairs = [(c[0], c[0], 0) for c in CONCEPTS] + [(a, d, 1) for a, d in ANCESTORS]
    insert(conn, "vocab.concept_ancestor", [
        {"ancestor_concept_id": a, "descendant_concept_id": d,
         "min_levels_of_separation": n, "max_levels_of_separation": n}
        for a, d, n in pairs
    ])  # fmt: skip


def numbered(rows: Iterator[dict[str, object]], id_column: str) -> list[dict[str, object]]:
    """Rows with a 1-based id column."""
    return [{id_column: n, **row} for n, row in enumerate(rows, start=1)]


def load(conn: Connection, data: Dataset) -> None:
    """Write vocabulary, cdm rows and identities (as the owner, tenant_id set explicitly)."""
    load_vocabulary(conn)
    patients = {r["patient_id"]: r for r in data.truth("patients")}

    def who(patient_id: str) -> dict[str, object]:
        tenant, person_id = data.persons[patient_id]
        return {"tenant_id": tenant, "person_id": person_id}

    insert(conn, "cdm.person", [
        who(p) | {"gender_concept_id": GENDER[r["sex"]],
                  "year_of_birth": int(r["birth_date"][:4]), "race_concept_id": 0,
                  "ethnicity_concept_id": 0, "gender_source_value": r["sex"]}
        for p, r in patients.items()
    ])  # fmt: skip
    insert(conn, "app.patient_identity", [
        who(p) | {"mrn": r["mrn"], "given_name": r["first_name"], "family_name": r["last_name"],
                  "birth_date": date.fromisoformat(r["birth_date"])}
        for p, r in patients.items()
    ])  # fmt: skip
    procedures = [
        who(p) | {"procedure_concept_id": TRANSPLANT[(r["organ"], r["donor_type"])],
                  "procedure_date": date.fromisoformat(r["transplant_date"]),
                  "procedure_source_value": r["organ"] + " transplant"}
        for p, r in patients.items()
    ] + [
        who(b["patient_id"]) | {"procedure_concept_id": BIOPSY[b["organ"]],
                                "procedure_date": date.fromisoformat(b["biopsy_date"]),
                                "procedure_source_value": f"biopsy of {b['organ']}"}
        for b in data.truth("biopsies")
    ]  # fmt: skip
    insert(conn, "cdm.procedure_occurrence", numbered(
        (r | {"procedure_type_concept_id": 32817} for r in procedures), "procedure_occurrence_id"
    ))  # fmt: skip
    insert(
        conn,
        "cdm.condition_occurrence",
        numbered(conditions(data, patients), "condition_occurrence_id"),
    )
    insert(conn, "cdm.measurement", numbered((
        who(lab["patient_id"]) | {
            "measurement_concept_id": LAB_CONCEPTS.get(lab["lab"], 0),
            "measurement_source_concept_id": DSA_LOCAL_CONCEPT if lab["lab"] == "dsa_mfi" else 0,
            "measurement_date": datetime.fromisoformat(lab["collected_at"]).date(),
            "measurement_datetime": datetime.fromisoformat(lab["collected_at"]),
            "measurement_type_concept_id": 32856, "value_as_number": float(lab["value"]),
            "unit_source_value": lab["unit"], "measurement_source_value": lab["lab"]}
        for lab in data.truth("labs")
    ), "measurement_id"))  # fmt: skip
    # Like the pipeline: an open order runs to the extract date (or death); verbatim stays NULL.
    deaths = {o["patient_id"]: date.fromisoformat(o["death_date"])
              for o in data.truth("outcomes") if o["death_date"]}  # fmt: skip
    cutoff = GeneratorConfig().data_cutoff
    insert(conn, "cdm.drug_exposure", numbered((
        who(m["patient_id"]) | {
            "drug_concept_id": DRUGS.get(m["rxnorm"], 0),
            "drug_exposure_start_date": date.fromisoformat(m["start_date"]),
            "drug_exposure_end_date": date.fromisoformat(m["end_date"]) if m["end_date"]
            else min(deaths.get(m["patient_id"], cutoff), cutoff),
            "verbatim_end_date": date.fromisoformat(m["end_date"]) if m["end_date"] else None,
            "drug_type_concept_id": 32838, "drug_source_value": m["drug"]}
        for m in data.truth("medications")
    ), "drug_exposure_id"))  # fmt: skip
    insert(conn, "cdm.death", [
        who(o["patient_id"]) | {"death_date": date.fromisoformat(o["death_date"]),
                                "death_type_concept_id": 32817}
        for o in data.truth("outcomes") if o["death_date"]
    ])  # fmt: skip
    # Follow-up ends at the patient's last follow-up date (the pipeline's observation period).
    insert(conn, "cdm.observation_period", numbered((
        who(o["patient_id"]) | {
            "observation_period_start_date": date.fromisoformat(
                patients[o["patient_id"]]["transplant_date"]),
            "observation_period_end_date": date.fromisoformat(o["last_follow_up_date"]),
            "period_type_concept_id": 32817}
        for o in data.truth("outcomes")
    ), "observation_period_id"))  # fmt: skip
    # Delayed graft function as the note NLP records it: "History of event" valued with DGF.
    insert(conn, "cdm.observation", numbered((
        who(p) | {"observation_concept_id": HISTORY_OF_EVENT, "value_as_concept_id": DGF,
                  "observation_date": date.fromisoformat(r["transplant_date"]),
                  "observation_type_concept_id": 32858}
        for p, r in patients.items() if r["dgf"] == "true"
    ), "observation_id"))  # fmt: skip
    insert(conn, "cdm.note", numbered((
        who(n["patient_id"]) | {
            "note_date": date.fromisoformat(n["note_date"]), "note_type_concept_id": 32831,
            "note_class_concept_id": 0, "note_title": n["note_type"],
            "note_text": (data.root / "raw" / n["file"]).read_text(),
            "encoding_concept_id": 0, "language_concept_id": 0,
            "note_source_value": n["note_id"]}
        for n in data.truth("notes")
    ), "note_id"))  # fmt: skip


def conditions(data: Dataset, patients: dict[str, dict[str, str]]) -> Iterator[dict[str, object]]:
    """Primary disease, diabetes and hypertension at transplant; graft failure when it happened;
    biopsy-proven rejection (TCMR, ABMR, mixed) on the biopsy date."""
    graft_loss = {o["patient_id"]: o["graft_loss_date"] for o in data.truth("outcomes")}
    for e in data.truth("rejection_episodes"):
        if e["rejection_type"] in REJECTION:
            tenant, person_id = data.persons[e["patient_id"]]
            yield {"tenant_id": tenant, "person_id": person_id, "condition_type_concept_id": 32817,
                   "condition_concept_id": REJECTION[e["rejection_type"]],
                   "condition_start_date": date.fromisoformat(e["biopsy_date"])}  # fmt: skip
    for p, r in patients.items():
        tenant, person_id = data.persons[p]
        base = {"tenant_id": tenant, "person_id": person_id, "condition_type_concept_id": 32840}
        at_transplant = date.fromisoformat(r["transplant_date"])
        concepts = [PRIMARY[r["organ"]]]
        concepts += [201826] if r["diabetes"] == "true" else []
        concepts += [316866] if r["hypertension"] == "true" else []
        for concept in concepts:
            yield base | {"condition_concept_id": concept, "condition_start_date": at_transplant}
        if graft_loss.get(p):
            yield base | {"condition_concept_id": GRAFT_FAILURE[r["organ"]],
                          "condition_start_date": date.fromisoformat(graft_loss[p])}  # fmt: skip
