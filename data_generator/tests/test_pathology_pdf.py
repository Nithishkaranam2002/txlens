import csv
from pathlib import Path

from txlens_datagen.models import PatientRecord
from txlens_datagen.rand import rng_for
from txlens_datagen.writers.pathology_pdf import report_lines

MAX_PDF_BYTES = 300_000


def test_every_biopsy_has_an_image_only_pdf(generated: Path) -> None:
    with (generated / "ground_truth" / "biopsies.csv").open(encoding="utf-8") as handle:
        biopsies = list(csv.DictReader(handle))
    assert biopsies
    for biopsy in biopsies:
        data = (generated / "raw" / biopsy["pdf_file"]).read_bytes()
        assert data.startswith(b"%PDF")
        assert b"/DCTDecode" in data
        assert b"/Font" not in data
        assert len(data) < MAX_PDF_BYTES
    pdfs = list((generated / "raw" / "pathology").rglob("*.pdf"))
    assert len(pdfs) == len(biopsies)


def test_report_text_matches_ground_truth(cohort: list[PatientRecord]) -> None:
    checked = 0
    for record in cohort[:400]:
        for biopsy in record.biopsies:
            text = "\n".join(t for _, t in report_lines(biopsy, record.patient, "X", rng_for(0)))
            assert record.patient.mrn in text
            assert f"{biopsy.biopsy_date:%m/%d/%Y}" in text
            if biopsy.organ == "kidney":
                assert f"i{biopsy.banff_i} t{biopsy.banff_t} v{biopsy.banff_v}" in text
                assert f"C4d{biopsy.c4d}" in text
                if biopsy.banff_tcmr_grade:
                    assert f"Banff grade {biopsy.banff_tcmr_grade}" in text
                if biopsy.diagnosis == "no_rejection":
                    assert "No evidence of rejection." in text
            else:
                assert f"ISHLT grade {biopsy.ishlt_acr_grade}" in text
                assert biopsy.pamr_grade in text
            if biopsy.other_finding:
                assert biopsy.other_finding in text
            checked += 1
    assert checked > 50
