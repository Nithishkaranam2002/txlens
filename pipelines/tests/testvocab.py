"""A tiny Athena-format vocabulary for pipeline tests.

Concept ids and codes are the real ones our rules use (they appear in our code anyway); every
name is "TEST ONLY - ...", so no licensed vocabulary text is committed. Our reviewed mapping
files are copied in, as `make vocab-subset` does.
"""

import shutil
from pathlib import Path

from txlens_omop.mappings import LOCAL_CONCEPT_FILE, STCM_FILE, mappings_dir

# concept_id, domain, vocabulary, class, standard, code, label
CONCEPTS = [
    (3016723, "Measurement", "LOINC", "Lab Test", "S", "2160-0", "creatinine"),
    (1619025, "Measurement", "LOINC", "Lab Test", "S", "98979-8", "egfr"),
    (46236053, "Measurement", "LOINC", "Lab Test", "S", "77348-1", "tacrolimus trough"),
    (3966008, "Measurement", "LOINC", "Lab Test", "S", "103142-6", "dd-cfdna"),
    (8840, "Unit", "UCUM", "Unit", "S", "mg/dL", "mg/dL"),
    (720870, "Unit", "UCUM", "Unit", "S", "mL/min/(173.10*-2.m2)", "egfr unit"),
    (8842, "Unit", "UCUM", "Unit", "S", "ng/mL", "ng/mL"),
    (8554, "Unit", "UCUM", "Unit", "S", "%", "percent"),
    (950637, "Drug", "RxNorm", "Ingredient", "S", "42316", "tacrolimus"),
    (19003999, "Drug", "RxNorm", "Ingredient", "S", "68149", "mycophenolate mofetil"),
    (19012565, "Drug", "RxNorm", "Ingredient", "S", "265323", "mycophenolate"),
    (1551099, "Drug", "RxNorm", "Ingredient", "S", "8640", "prednisone"),
    (1506270, "Drug", "RxNorm", "Ingredient", "S", "6902", "methylprednisolone"),
    (
        19136207,
        "Drug",
        "RxNorm",
        "Ingredient",
        "S",
        "1011",
        "lymphocyte immune globulin, anti-thymocyte globulin",
    ),
    (19117912, "Drug", "RxNorm", "Ingredient", "S", "5666", "immunoglobulin G"),
    (1703063, "Drug", "RxNorm", "Ingredient", "S", "275891", "valganciclovir"),
    (19038654, "Drug", "RxNorm", "Brand Name", "", "TEST-BN-1", "Prograf"),
    (19023134, "Drug", "RxNorm", "Brand Name", "", "TEST-BN-2", "Cellcept"),
    (19048391, "Drug", "RxNorm", "Brand Name", "", "TEST-BN-3", "Solu-Medrol"),
    (19087946, "Drug", "RxNorm", "Brand Name", "", "TEST-BN-4", "Thymoglobulin"),
    (19011227, "Drug", "RxNorm", "Brand Name", "", "TEST-BN-5", "Valcyte"),
    (4322471, "Procedure", "SNOMED", "Procedure", "S", "70536003", "kidney transplant"),
    (4137127, "Procedure", "SNOMED", "Procedure", "S", "32413006", "heart transplant"),
    (1244937, "Procedure", "SNOMED", "Procedure", "S", "1293269004", "kidney graft biopsy"),
    (4308808, "Procedure", "SNOMED", "Procedure", "S", "387829002", "endomyocardial biopsy"),
    (193782, "Condition", "SNOMED", "Disorder", "S", "46177005", "esrd"),
    (201826, "Condition", "SNOMED", "Disorder", "S", "44054006", "type 2 diabetes"),
    (316866, "Condition", "SNOMED", "Disorder", "S", "38341003", "hypertension"),
    (316139, "Condition", "SNOMED", "Disorder", "S", "84114007", "heart failure"),
    (4121617, "Condition", "SNOMED", "Disorder", "S", "233934000", "cardiac transplant failure"),
    (4127554, "Condition", "SNOMED", "Disorder", "S", "236583003", "failed renal transplant"),
    (2100000900, "Condition", "SNOMED", "Disorder", "", "TEST-OLD-ESRD", "outdated esrd code"),
    (4132161, "Route", "SNOMED", "Qualifier Value", "S", "26643006", "oral"),
    (4171047, "Route", "SNOMED", "Qualifier Value", "S", "47625008", "intravenous"),
    (3000735, "Note", "LOINC", "Doc Kind", "S", "11506-3", "progress note"),
    (4180186, "Language", "SNOMED", "Qualifier Value", "S", "297487008", "english"),
    (8507, "Gender", "Gender", "Gender", "S", "M", "male"),
    (8532, "Gender", "Gender", "Gender", "S", "F", "female"),
]
# Brand Name -> ingredient (RxNorm "Brand name of").
RELATIONSHIPS = [
    (19038654, 950637, "Brand name of"),
    (19023134, 19003999, "Brand name of"),
    (19048391, 1506270, "Brand name of"),
    (19087946, 19136207, "Brand name of"),
    (19011227, 1703063, "Brand name of"),
]
SYNONYMS = [(950637, "TEST ONLY - FK-506 synonym")]
VALID = "19700101\t20991231"
HEADER = (
    "concept_id\tconcept_name\tdomain_id\tvocabulary_id\tconcept_class_id\tstandard_concept\t"
    "concept_code\tvalid_start_date\tvalid_end_date\tinvalid_reason\n"
)


def name(label: str, vocabulary: str) -> str:
    """Brand names and ingredient names must match exactly; others are marked TEST ONLY."""
    return label if vocabulary == "RxNorm" else f"TEST ONLY - {label}"


def write_vocab(folder: Path) -> Path:
    """Write the test vocabulary in Athena format into `folder`."""
    folder.mkdir(parents=True, exist_ok=True)
    rows = [
        f"{cid}\t{name(label, vocab)}\t{domain}\t{vocab}\t{cls}\t{std}\t{code}\t{VALID}\t\n"
        for cid, domain, vocab, cls, std, code, label in CONCEPTS
    ]
    (folder / "CONCEPT.csv").write_text(HEADER + "".join(rows))
    (folder / "CONCEPT_RELATIONSHIP.csv").write_text(
        "concept_id_1\tconcept_id_2\trelationship_id\tvalid_start_date\tvalid_end_date\t"
        "invalid_reason\n"
        + "".join(f"{a}\t{b}\t{r}\t19700101\t20991231\t\n" for a, b, r in RELATIONSHIPS)
        + "2100000900\t193782\tMaps to\t19700101\t20991231\t\n"
    )
    (folder / "VOCABULARY.csv").write_text(
        "vocabulary_id\tvocabulary_name\tvocabulary_reference\tvocabulary_version\t"
        "vocabulary_concept_id\nNone\tOMOP Standardized Vocabularies\tTEST\tv5.0 TEST\t44819096\n"
    )
    (folder / "CONCEPT_SYNONYM.csv").write_text(
        "concept_id\tconcept_synonym_name\tlanguage_concept_id\n"
        + "".join(f"{c}\t{n}\t4180186\n" for c, n in SYNONYMS)
    )
    for file_name in (LOCAL_CONCEPT_FILE, STCM_FILE):
        shutil.copyfile(mappings_dir() / file_name, folder / file_name)
    return folder
