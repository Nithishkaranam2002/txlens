"""Center lab feeds: one CSV per center, each with its own columns, units and date format.

Mess is injected on purpose (duplicates, bad dates, test-name variants, stray whitespace,
missing values) and every injected problem is recorded so cleaning can be scored later.
"""

import _csv
import csv
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TextIO

from txlens_datagen.config import Center, MessConfig
from txlens_datagen.models import LabName, LabResult, PatientRecord
from txlens_datagen.rand import Rng, chance, pick

UMOL_PER_MG_DL = 88.4

# LOINC codes for center A's coded rows. Labs without a code here are always uncoded.
# Checked against Athena v20260829 with `make vocab-check`.
LOINC: dict[LabName, str] = {
    "creatinine": "2160-0",  # Creatinine [Mass/volume] in Serum or Plasma
    "egfr": "98979-8",  # GFR/1.73 sq M ... by Creatinine-based formula (CKD-EPI 2021)
    "tacrolimus_trough": "77348-1",  # Tacrolimus [Mass/volume] in Blood --trough
}

Cells = list[str]
Formatter = Callable[[LabResult, str, MessConfig, Rng], tuple[Cells, list[str]]]


@dataclass(frozen=True)
class Dialect:
    """How one center's lab system writes its export."""

    file_name: str
    delimiter: str
    header: tuple[str, ...]
    name_col: int
    value_col: int
    date_col: int
    test_names: dict[LabName, tuple[str, ...]]
    bad_dates: tuple[str, ...]
    format_row: Formatter


def comma_decimal(text: str) -> str:
    """European decimal comma: '1.5' -> '1,5'."""
    return text.replace(".", ",")


def format_a(lab: LabResult, mrn: str, mess: MessConfig, rng: Rng) -> tuple[Cells, list[str]]:
    """Center A: ISO dates, mg/dL, dd-cfDNA as '0.32%', LOINC on some rows."""
    value = {
        "creatinine": f"{lab.value:.2f}",
        "egfr": f"{lab.value:.0f}",
        "tacrolimus_trough": f"{lab.value:.1f}",
        "dd_cfdna": f"{lab.value:.2f}%",
        "dsa_mfi": f"{lab.value:.0f}",
    }[lab.lab]
    unit = {"egfr": "mL/min/1.73m2", "dd_cfdna": ""}.get(lab.lab, lab.unit)
    loinc = LOINC.get(lab.lab, "") if chance(rng, mess.loinc_coded_rate) else ""
    name = DIALECTS["a"].test_names[lab.lab][0]
    return [mrn, name, loinc, value, unit, f"{lab.collected_at:%Y-%m-%d %H:%M}"], []


def format_b(lab: LabResult, mrn: str, mess: MessConfig, rng: Rng) -> tuple[Cells, list[str]]:
    """Center B: semicolons, decimal commas, day-first dates, umol/L creatinine, fractions."""
    issues = []
    if lab.lab == "creatinine":
        value, unit = f"{round(lab.value * UMOL_PER_MG_DL)}", "µmol/L"
    elif lab.lab == "egfr":
        value, unit = (
            (">90", "mL/min/1.73m²") if lab.value > 90 else (f"{lab.value:.0f}", "mL/min/1.73m²")
        )
        if lab.value > 90:
            issues.append("censored")
    elif lab.lab == "tacrolimus_trough":
        value, unit = comma_decimal(f"{lab.value:.1f}"), "µg/L"
    elif lab.lab == "dd_cfdna":
        value, unit = comma_decimal(f"{lab.value / 100:.4f}"), "fraction"
    else:
        value, unit = f"{lab.value:.0f}", "MFI"
    name = DIALECTS["b"].test_names[lab.lab][0]
    return [mrn, name, value, unit, f"{lab.collected_at:%d/%m/%Y %H:%M}"], issues


def us_datetime(when: datetime) -> str:
    """'4/5/2023 8:30 AM' style timestamp."""
    hour = when.hour % 12 or 12
    return f"{when.month}/{when.day}/{when.year} {hour}:{when.minute:02d} {when:%p}"


def format_c(lab: LabResult, mrn: str, mess: MessConfig, rng: Rng) -> tuple[Cells, list[str]]:
    """Center C: local lab codes, US dates, creatinine units mixed row by row, '<0.05' dd-cfDNA."""
    issues = []
    code = {
        "creatinine": "CRE",
        "egfr": "EGFR",
        "tacrolimus_trough": "TACT",
        "dd_cfdna": "DDCF",
        "dsa_mfi": "DSAMFI",
    }[lab.lab]
    if lab.lab == "creatinine" and chance(rng, 0.3):
        value, unit = f"{round(lab.value * UMOL_PER_MG_DL)}", "umol/L"
    elif lab.lab == "creatinine":
        value, unit = f"{lab.value:.2f}", "mg/dL"
    elif lab.lab == "dd_cfdna" and lab.value < 0.05:
        value, unit = "<0.05", "%"
        issues.append("censored")
    elif lab.lab == "dd_cfdna":
        value, unit = f"{lab.value:.2f}", "%"
    elif lab.lab == "tacrolimus_trough":
        value, unit = f"{lab.value:.1f}", "ng/mL"
    else:
        value, unit = f"{lab.value:.0f}", lab.unit
    name = DIALECTS["c"].test_names[lab.lab][0]
    return [mrn, code, name, value, unit, us_datetime(lab.collected_at)], issues


DIALECTS: dict[str, Dialect] = {
    "a": Dialect(
        file_name="center_a_labs.csv",
        delimiter=",",
        header=("MRN", "TEST_NAME", "LOINC", "RESULT", "UNITS", "COLLECTED_DT"),
        name_col=1,
        value_col=3,
        date_col=5,
        test_names={
            "creatinine": ("CREATININE", "CREATININ", "Creatinine, serum", "CREATININE SERUM"),
            "egfr": ("EGFR CKD-EPI 2021", "eGFR", "EGFR (CKD-EPI)"),
            "tacrolimus_trough": ("TACROLIMUS TROUGH", "TACROLIMUS TROUGH LVL", "TACRO LEVEL"),
            "dd_cfdna": ("DD-CFDNA", "DD CFDNA", "dd-cfDNA %"),
            "dsa_mfi": ("DSA MAX MFI", "DSA MFI"),
        },
        bad_dates=("N/A", "2023-13-05 08:00", "0000-00-00 00:00", ""),
        format_row=format_a,
    ),
    "b": Dialect(
        file_name="center_b_labs.csv",
        delimiter=";",
        header=("patient_id", "analyte", "value", "unit", "collection_date"),
        name_col=1,
        value_col=2,
        date_col=4,
        test_names={
            "creatinine": ("Creatinine (enzymatic)", "creatinine", "Creatinine enz."),
            "egfr": ("eGFR", "eGFR CKD-EPI", "GFR estimated"),
            "tacrolimus_trough": ("Tacrolimus", "Tacrolimus C0", "tacrolimus level"),
            "dd_cfdna": ("Donor-derived cfDNA", "dd-cfDNA fraction", "Donor derived cf-DNA"),
            "dsa_mfi": ("DSA (max MFI)", "DSA max"),
        },
        bad_dates=("31/02/2024 08:00", "unknown", "00/00/0000 00:00"),
        format_row=format_b,
    ),
    "c": Dialect(
        file_name="center_c_labs.csv",
        delimiter=",",
        header=("PatientMRN", "LabCode", "LabDescription", "ResultValue", "ResultUnit", "DrawnAt"),
        name_col=2,
        value_col=3,
        date_col=5,
        test_names={
            "creatinine": ("Creatinine", "Creatinine Lvl", "CREAT"),
            "egfr": ("eGFR", "Estimated GFR"),
            "tacrolimus_trough": (
                "Tacrolimus Trough",
                "Tacrolimus (FK506) Trough",
                "Tacro trough lvl",
            ),
            "dd_cfdna": ("dd-cfDNA", "Donor Derived Cell Free DNA", "dd-cfDNA assay"),
            "dsa_mfi": ("DSA Max MFI", "Donor Specific Ab MFI"),
        },
        bad_dates=("13/45/2023 7:00 AM", "TBD", ""),
        format_row=format_c,
    ),
}


@dataclass(frozen=True)
class RawLocation:
    """Where a lab result landed in the raw feed."""

    raw_file: str
    raw_row: int


@dataclass(frozen=True)
class FeedIssue:
    """One injected problem on one raw row."""

    raw_file: str
    raw_row: int
    lab_id: str
    issue: str


def inject_mess(
    cells: Cells, issues: list[str], lab: LabName, dialect: Dialect, mess: MessConfig, rng: Rng
) -> None:
    """Corrupt one formatted row in place, recording each problem."""
    variants = dialect.test_names[lab][1:]
    if variants and chance(rng, mess.name_variant_rate):
        cells[dialect.name_col] = pick(rng, variants)
        issues.append("name_variant")
    if chance(rng, mess.whitespace_rate):
        cells[dialect.value_col] = f"  {cells[dialect.value_col]} "
        issues.append("whitespace")
    if chance(rng, mess.missing_value_rate):
        cells[dialect.value_col] = ""
        issues.append("missing_value")
    if chance(rng, mess.bad_date_rate):
        cells[dialect.date_col] = pick(rng, dialect.bad_dates)
        issues.append("bad_date")


@dataclass
class LabFeedWriter:
    """Streams lab rows into one open CSV per center."""

    out_dir: Path
    mess: MessConfig
    files: dict[str, TextIO] = field(default_factory=dict)
    writers: dict[str, _csv.Writer] = field(default_factory=dict)
    row_counts: dict[str, int] = field(default_factory=dict)

    def open(self, centers: tuple[Center, ...]) -> None:
        """Create each center's file and write its header."""
        self.out_dir.mkdir(parents=True, exist_ok=True)
        for center in centers:
            dialect = DIALECTS[center.code]
            handle = (self.out_dir / dialect.file_name).open("w", newline="", encoding="utf-8")
            writer = csv.writer(handle, delimiter=dialect.delimiter)
            writer.writerow(dialect.header)
            self.files[center.code], self.writers[center.code] = handle, writer
            self.row_counts[center.code] = 0

    def write_row(self, code: str, cells: Cells) -> int:
        """Write one data row and return its 1-based row number (header excluded)."""
        self.writers[code].writerow(cells)
        self.row_counts[code] += 1
        return self.row_counts[code]

    def add(
        self, record: PatientRecord, center: Center, rng: Rng
    ) -> tuple[dict[str, RawLocation], list[FeedIssue]]:
        """Write one patient's labs; return where each landed and what was corrupted."""
        dialect = DIALECTS[center.code]
        locations: dict[str, RawLocation] = {}
        problems: list[FeedIssue] = []
        for lab in record.labs:
            cells, issues = dialect.format_row(lab, record.patient.mrn, self.mess, rng)
            inject_mess(cells, issues, lab.lab, dialect, self.mess, rng)
            row = self.write_row(center.code, cells)
            locations[lab.lab_id] = RawLocation(dialect.file_name, row)
            problems += [FeedIssue(dialect.file_name, row, lab.lab_id, i) for i in issues]
            if chance(rng, self.mess.duplicate_rate):
                copy = self.write_row(center.code, cells)
                problems.append(FeedIssue(dialect.file_name, copy, lab.lab_id, "duplicate"))
        return locations, problems

    def close(self) -> None:
        """Close all files."""
        for handle in self.files.values():
            handle.close()
