"""Local mappings that no standard vocabulary provides.

Two files, in Athena's tab-separated format, ship with this package (they are our own content,
not licensed vocabulary):

- `SOURCE_TO_CONCEPT_MAP.csv`: lab local codes and test-name variants -> LOINC, clinical
  drug abbreviations -> RxNorm ingredient, uncoded transplant procedure texts -> SNOMED,
  free-text condition names -> SNOMED, and clinic-note terms and abbreviations for the note
  NLP. Every target is a standard concept (or one of our local concepts); `make vocab-check`
  re-checks each one against the loaded vocabulary.
  A "history of" text follows the OMOP pattern: its TXLENS_CONDITION row names the standard
  "History of event" (Observation) and its TXLENS_COND_VALUE row the procedure that goes
  into observation.value_as_concept_id.
  The TXLENS_CONDITION, TXLENS_COND_VALUE and TXLENS_NOTE_ABBREV rows were curated by an
  AI assistant and are pending clinical review (CURATED_PENDING_REVIEW); no clinician has
  checked them yet.
- `LOCAL_CONCEPT.csv`: TxLens local concepts (id >= 2,000,000,000, non-standard) for things no
  standard vocabulary has, such as a numeric donor-specific antibody MFI.

Source codes are matched exactly after `normalize_source_code`: never by substring.
"""

import csv
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

STCM_FILE = "SOURCE_TO_CONCEPT_MAP.csv"
LOCAL_CONCEPT_FILE = "LOCAL_CONCEPT.csv"
LOCAL_VOCABULARY = "TxLens"
LOCAL_CONCEPT_MIN = 2_000_000_000
# Our source vocabularies and the OMOP domains their (standard) targets may have.
SOURCE_VOCABULARIES = {
    "TXLENS_LAB_NAME": ("Measurement",),
    "TXLENS_LAB_CODE": ("Measurement",),
    "TXLENS_DRUG": ("Drug",),
    "TXLENS_PROCEDURE": ("Procedure",),
    "TXLENS_CONDITION": ("Condition", "Observation"),
    # The value of a "history of" observation: same source code as its TXLENS_CONDITION row.
    "TXLENS_COND_VALUE": ("Procedure", "Condition"),
    # Clinic-note terms and abbreviations (Cr, tacro, DGF, KTx). A source code may start with
    # "kidney:" or "heart:" when the term means different concepts per transplanted organ.
    "TXLENS_NOTE_ABBREV": ("Condition", "Drug", "Measurement", "Procedure"),
}
CURATED_PENDING_REVIEW = frozenset({"TXLENS_CONDITION", "TXLENS_COND_VALUE", "TXLENS_NOTE_ABBREV"})
PENDING_REVIEW_LABEL = "curated by AI assistant, pending clinical review"


@dataclass(frozen=True)
class MappingRow:
    """One source_to_concept_map row (the columns we use)."""

    source_code: str
    source_vocabulary_id: str
    source_code_description: str
    target_concept_id: int
    target_vocabulary_id: str


def mappings_dir() -> Path:
    """Folder holding the mapping files inside the installed package."""
    return Path(str(files("txlens_omop") / "mappings"))


def normalize_source_code(text: str) -> str:
    """Lower case, trimmed, inner whitespace collapsed: 'TACRO  Level ' -> 'tacro level'."""
    return re.sub(r"\s+", " ", text.strip().lower())


def read_mappings(path: Path | None = None) -> list[MappingRow]:
    """All rows of the source_to_concept_map file."""
    source = path or mappings_dir() / STCM_FILE
    with source.open(encoding="utf-8", newline="") as handle:
        return [
            MappingRow(
                row["source_code"],
                row["source_vocabulary_id"],
                row["source_code_description"],
                int(row["target_concept_id"]),
                row["target_vocabulary_id"],
            )
            for row in csv.DictReader(handle, delimiter="\t")
        ]
