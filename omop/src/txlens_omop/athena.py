"""Read an Athena vocabulary download (zip or folder) as a stream of lines.

Athena files are tab-separated with a header row, no quoting, and dates as YYYYMMDD. They are
large (CONCEPT_ANCESTOR alone is ~1.6 GB), so they are never fully read into memory, and a zip
is read directly without unpacking it to disk.

Licensing: Athena, SNOMED CT and RxNorm content must not be redistributed. The download lives
in the git-ignored `omop/athena/` folder.
"""

import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO

# The big clinical vocabularies we use. Only these are filtered concept by concept in subsets.
CLINICAL_VOCABULARIES = frozenset({"SNOMED", "LOINC", "RxNorm", "RxNorm Extension", "ICD10CM"})
# Small vocabularies for units, types, visits, demographics and the vocabulary's own metadata.
SUPPORT_VOCABULARIES = frozenset(
    {
        "UCUM", "Gender", "Race", "Ethnicity", "Visit", "Visit Type", "Type Concept",
        "Note Type", "Meas Type", "Condition Type", "Drug Type", "Procedure Type",
        "Observation Type", "Death Type", "Obs Period Type", "Condition Status",
        "CMS Place of Service", "Language", "Domain", "Vocabulary", "Concept Class",
        "Relationship", "Metadata", "CDM", "OMOP Extension", "None",
    }
)  # fmt: skip
VOCABULARIES = CLINICAL_VOCABULARIES | SUPPORT_VOCABULARIES
# Without these the platform cannot map its data, so a load that lacks them fails.
REQUIRED_VOCABULARIES = frozenset({"SNOMED", "LOINC", "RxNorm"})
# Never loaded, even if requested.
EXCLUDED_VOCABULARIES = {
    "CPT4": "needs a UMLS licence; Athena ships it without concept names",
}

# Table -> file name in the download.
TABLE_FILES = {
    "vocabulary": "VOCABULARY.csv",
    "domain": "DOMAIN.csv",
    "concept_class": "CONCEPT_CLASS.csv",
    "relationship": "RELATIONSHIP.csv",
    "concept": "CONCEPT.csv",
    "concept_relationship": "CONCEPT_RELATIONSHIP.csv",
    "concept_ancestor": "CONCEPT_ANCESTOR.csv",
    "concept_synonym": "CONCEPT_SYNONYM.csv",
}
# Tables whose rows link concepts: kept only if every linked concept was kept.
CONCEPT_ID_COLUMNS = {
    "concept_relationship": (0, 1),
    "concept_ancestor": (0, 1),
    "concept_synonym": (0,),
}
VOCABULARY_ID_COLUMN = 3  # position of vocabulary_id in CONCEPT.csv


def wanted_vocabularies(requested: frozenset[str] = VOCABULARIES) -> frozenset[str]:
    """Requested vocabularies minus the excluded ones."""
    return frozenset(requested - EXCLUDED_VOCABULARIES.keys())


@dataclass(frozen=True)
class AthenaSource:
    """An Athena download: a zip file or a folder of CSV files."""

    path: Path

    @classmethod
    def find(cls, folder: Path) -> "AthenaSource":
        """The single zip in `folder`, or the folder itself if it holds CONCEPT.csv."""
        zips = sorted(folder.glob("*.zip"))
        if len(zips) == 1:
            return cls(zips[0])
        if len(zips) > 1:
            raise FileNotFoundError(f"More than one zip in {folder}; pass the one to use.")
        if any(p.name.upper() == "CONCEPT.CSV" for p in folder.glob("*")):
            return cls(folder)
        raise FileNotFoundError(f"No Athena zip or CONCEPT.csv in {folder}.")

    def member_name(self, archive: zipfile.ZipFile, file_name: str) -> str:
        """Name of a file inside the zip, ignoring case and sub-folders."""
        for name in archive.namelist():
            if Path(name).name.upper() == file_name.upper():
                return name
        raise FileNotFoundError(f"{file_name} not found in {self.path.name}")

    @contextmanager
    def open(self, file_name: str) -> Iterator[IO[bytes]]:
        """Open one file of the download as a binary stream (zip members are not unpacked)."""
        if self.path.is_dir():
            matches = [p for p in self.path.iterdir() if p.name.upper() == file_name.upper()]
            if not matches:
                raise FileNotFoundError(f"{file_name} not found in {self.path}")
            with matches[0].open("rb") as handle:
                yield handle
            return
        with (
            zipfile.ZipFile(self.path) as archive,
            archive.open(self.member_name(archive, file_name)) as handle,
        ):
            yield handle

    @contextmanager
    def rows(self, table: str) -> Iterator[tuple[list[str], Iterator[bytes]]]:
        """Header columns and an iterator over raw data lines (each ending in a newline)."""
        with self.open(TABLE_FILES[table]) as handle:
            header = handle.readline().decode("utf-8").rstrip("\r\n").lower().split("\t")
            yield header, (line.rstrip(b"\r\n") + b"\n" for line in handle if line.strip())


def links_kept(line: bytes, columns: tuple[int, ...], kept: set[bytes]) -> bool:
    """True if every concept id the row links to is in `kept`."""
    parts = line.split(b"\t", max(columns) + 1)
    return all(parts[c] in kept for c in columns)
