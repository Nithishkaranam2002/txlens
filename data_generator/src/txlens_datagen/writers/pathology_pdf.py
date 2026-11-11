"""Biopsy pathology reports rendered as scanned-looking, image-only PDFs.

The text is drawn onto a page image, then the page is slightly rotated, blurred, toned and
speckled like a photocopy, and saved as a JPEG-compressed PDF with no text layer. Getting the
text back needs OCR, which is exactly what the document pipeline must do.
"""

from datetime import datetime, time, timedelta
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from txlens_datagen.cohort import FIRST_NAMES, make_surname
from txlens_datagen.models import Biopsy, Patient
from txlens_datagen.rand import Rng, pick, randint, uniform

PAGE_INCHES = (8.5, 11.0)
MARGIN_INCHES = 0.75
JPEG_QUALITY = 55
# Glomeruli counted, by specimen adequacy (Banff: adequate >= 10, marginal 7-9).
GLOMERULI = {
    "adequate": (10, 28),
    "marginal": (7, 9),
    "inadequate": (3, 6),
    "not_reported": (10, 28),
}
ACR_SEVERITY = {"0R": "no acute cellular rejection", "1R": "mild", "2R": "moderate", "3R": "severe"}
TITLE, HEADING, BODY = "title", "heading", "body"
Line = tuple[str, str]


def kidney_diagnosis(b: Biopsy) -> list[str]:
    """Diagnosis lines for a kidney allograft biopsy."""
    lines = []
    if b.diagnosis == "no_rejection":
        lines.append("No evidence of rejection.")
    if b.diagnosis == "borderline":
        lines.append("Borderline (suspicious) for acute T-cell mediated rejection.")
    if b.diagnosis in ("TCMR", "mixed"):
        lines.append(f"Acute T-cell mediated rejection, Banff grade {b.banff_tcmr_grade}.")
    if b.diagnosis in ("ABMR", "mixed"):
        c4d = "C4d positive" if (b.c4d or 0) >= 2 else "C4d negative"
        lines.append(f"Active antibody-mediated rejection ({c4d}).")
    if b.other_finding:
        lines.append(f"{b.other_finding}.")
    lines.append(f"Interstitial fibrosis / tubular atrophy: ci{b.banff_ci}, ct{b.banff_ct}.")
    return lines


def heart_diagnosis(b: Biopsy) -> list[str]:
    """Diagnosis lines for an endomyocardial biopsy."""
    severity = ACR_SEVERITY[b.ishlt_acr_grade]
    acr = (
        "No acute cellular rejection, ISHLT grade 0R."
        if b.ishlt_acr_grade == "0R"
        else f"Acute cellular rejection, ISHLT grade {b.ishlt_acr_grade} ({severity})."
    )
    amr = "negative" if b.pamr_grade == "pAMR 0" else "positive"
    return [acr, f"Pathologic antibody-mediated rejection: {b.pamr_grade} ({amr})."]


def microscopic(b: Biopsy, rng: Rng) -> list[str]:
    """Microscopic description with lesion scores."""
    if b.organ == "heart":
        return [
            f"{randint(rng, 3, 6)} fragments of endomyocardium examined.",
            f"ACR grade (ISHLT 2004): {b.ishlt_acr_grade}",
            f"pAMR grade (ISHLT 2013): {b.pamr_grade}",
        ]
    glomeruli = randint(rng, *GLOMERULI[b.adequacy or "not_reported"])
    sclerosed = randint(rng, 0, min(3, glomeruli - 1))
    sv40 = "positive" if b.other_finding.startswith("Polyomavirus") else "negative"
    cv = "" if b.banff_cv == "not_reported" else f" cv{b.banff_cv}"
    adequacy = [] if b.adequacy == "not_reported" else [f"Specimen adequacy: {b.adequacy}"]
    return [
        f"Glomeruli: {glomeruli} ({sclerosed} globally sclerosed)",
        *adequacy,
        f"Banff lesion scores: i{b.banff_i} t{b.banff_t} v{b.banff_v} g{b.banff_g} "
        f"ptc{b.banff_ptc} ci{b.banff_ci} ct{b.banff_ct}{cv}",
        f"C4d (IHC, peritubular capillaries): C4d{b.c4d}",
        f"SV40 immunostain: {sv40}",
    ]


def report_lines(b: Biopsy, patient: Patient, center_name: str, rng: Rng) -> list[Line]:
    """All text on the report, as (style, text) pairs."""
    accession = f"S{b.biopsy_date:%y}-{randint(rng, 10000, 99999)}"
    reported = b.biopsy_date + timedelta(days=randint(rng, 1, 3))
    if b.organ == "kidney":
        specimen = "Kidney allograft, percutaneous needle core biopsy"
    else:
        specimen = "Endomyocardial biopsy, right ventricle"
    indication = "protocol surveillance" if b.indication == "protocol" else "for-cause"
    pathologist = f"{pick(rng, FIRST_NAMES['F'] + FIRST_NAMES['M'])} {make_surname(rng)}, MD"
    diagnosis = kidney_diagnosis(b) if b.organ == "kidney" else heart_diagnosis(b)
    return [
        (TITLE, center_name.upper()),
        (BODY, "Department of Pathology and Laboratory Medicine"),
        (HEADING, "SURGICAL PATHOLOGY REPORT"),
        (BODY, f"Patient: {patient.last_name.upper()}, {patient.first_name.upper()}"),
        (BODY, f"MRN: {patient.mrn}    DOB: {patient.birth_date:%m/%d/%Y}    Sex: {patient.sex}"),
        (BODY, f"Accession: {accession}"),
        (BODY, f"Procedure date: {b.biopsy_date:%m/%d/%Y}    Reported: {reported:%m/%d/%Y}"),
        (BODY, f"Specimen: {specimen}"),
        (BODY, f"Clinical history: {patient.organ} transplant {patient.transplant_date:%m/%d/%Y};"),
        (BODY, f"  biopsy indication: {indication}."),
        (HEADING, "MICROSCOPIC DESCRIPTION"),
        *((BODY, line) for line in microscopic(b, rng)),
        (HEADING, "DIAGNOSIS"),
        (BODY, f"{'Kidney' if b.organ == 'kidney' else 'Heart'} allograft, biopsy:"),
        *((BODY, f"  - {line}") for line in diagnosis),
        (BODY, ""),
        (BODY, f"Electronically signed: {pathologist}"),
    ]


def draw_page(lines: list[Line], dpi: int) -> Image.Image:
    """Draw clean black text on a white page."""
    width, height = (round(side * dpi) for side in PAGE_INCHES)
    margin = round(MARGIN_INCHES * dpi)
    sizes = {TITLE: dpi // 5, HEADING: dpi // 7, BODY: dpi // 8}
    fonts = {style: ImageFont.load_default(size=size) for style, size in sizes.items()}
    page = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(page)
    y = margin
    for style, text in lines:
        if style == HEADING:
            y += sizes[BODY]
            draw.line((margin, y - 4, width - margin, y - 4), fill=60, width=1)
        draw.text((margin, y), text, font=fonts[style], fill=15)
        y += round(sizes[style] * 1.6)
    return page


def scan_effects(page: Image.Image, rng: Rng) -> Image.Image:
    """Make a clean page look scanned: skew, blur, paper tone, grain and speckles."""
    page = page.rotate(uniform(rng, -1.5, 1.5), resample=Image.Resampling.BICUBIC, fillcolor=255)
    page = page.filter(ImageFilter.GaussianBlur(0.6))
    pixels = np.asarray(page).astype(np.float32) * 0.92 + 12
    pixels += rng.normal(0, 3, pixels.shape)
    count = randint(rng, 800, 2000)
    ys = rng.integers(0, pixels.shape[0], count)
    xs = rng.integers(0, pixels.shape[1], count)
    pixels[ys, xs] = rng.integers(60, 160, count)
    return Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))


def write_report(
    biopsy: Biopsy, patient: Patient, center_name: str, raw_dir: Path, dpi: int, rng: Rng
) -> str:
    """Write raw/pathology/<center>/<biopsy_id>.pdf and return its path relative to raw/."""
    relative = Path("pathology") / patient.tenant_id / f"{biopsy.biopsy_id}.pdf"
    path = raw_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    page = scan_effects(draw_page(report_lines(biopsy, patient, center_name, rng), dpi), rng)
    # A fixed "scanned on" date instead of now, so reruns produce byte-identical files.
    scanned = datetime.combine(biopsy.biopsy_date + timedelta(days=3), time(15, 0)).timetuple()
    page.save(
        path,
        "PDF",
        resolution=dpi,
        quality=JPEG_QUALITY,
        title="Surgical Pathology Report",
        creationDate=scanned,
        modDate=scanned,
    )
    return relative.as_posix()
