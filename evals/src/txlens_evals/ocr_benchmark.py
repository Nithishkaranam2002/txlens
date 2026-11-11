"""OCR alone (no LLM) on the biopsy reports: how many key tokens come out exactly right.

`make ocr-report` writes evals/reports/ocr.md. It reads the same stratified sample as the
extraction eval (biopsy_extraction.py) with several Tesseract setups and counts, per report,
whether each key token of the ground truth appears exactly in the OCR text:
- kidney: every Banff score ("t2"), C4d ("C4d0"), the TCMR grade ("IIA"), adequacy, the
  procedure date, the diagnosis and finding wording;
- heart: the ISHLT ACR grade ("2R"), pAMR ("pAMR 1 (I+)"), the date and the diagnosis wording.

`--render-dpi N` also re-renders the sampled reports at N dpi (into data/eval/, with the
generator's own random streams, so they equal what regenerating the data would produce) and
measures those too.
"""

import argparse
import re
import time
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from txlens_backend.documents.ocr import OcrResult, ocr_with_tesseract
from txlens_backend.documents.preprocess import OcrSettings
from txlens_datagen.config import GeneratorConfig
from txlens_datagen.rand import STREAM_PDF, rng_for
from txlens_datagen.record import build_record
from txlens_datagen.writers.pathology_pdf import write_report
from txlens_evals.biopsy_extraction import (
    DEFAULT_SAMPLE,
    SEED,
    Truth,
    patient_index,
    read_truth,
    stratified,
)

SCORE_LABELS = {
    "banff_i": "i",
    "banff_t": "t",
    "banff_v": "v",
    "banff_g": "g",
    "banff_ptc": "ptc",
    "banff_ci": "ci",
    "banff_ct": "ct",
    "banff_cv": "cv",
}
KIDNEY_WORDING = {
    "no_rejection": ("No evidence of rejection",),
    "borderline": ("Borderline (suspicious)",),
    "TCMR": ("T-cell mediated rejection",),
    "ABMR": ("antibody-mediated rejection",),
    "mixed": ("T-cell mediated rejection", "antibody-mediated rejection"),
}
FINDING_WORDING = {
    "BK_nephropathy": "Polyomavirus (BK) nephropathy",
    "CMV": "CMV immunostain positive",
    "ATN": "Acute tubular injury",
}
EDGE = re.compile(r"^[^\w]+|[^\w+)]+$")
# Every setup spelled out from a plain base, so changing the production defaults does not
# change what a row means.
PLAIN = OcrSettings(dpi=300, deskew=False, median=0, whitelist_rereads=False)
SETUPS: dict[str, OcrSettings] = {
    "A. Step 2.1 start (400 dpi, denoise, psm 4)": replace(PLAIN, dpi=400, median=3),
    "B. 300 dpi only": PLAIN,
    "C. 300 dpi + deskew": replace(PLAIN, deskew=True),
    "D. 300 dpi + deskew + denoise": replace(PLAIN, deskew=True, median=3),
    "E. D + Otsu": replace(PLAIN, deskew=True, median=3, binarize="otsu"),
    "F. D + adaptive threshold": replace(PLAIN, deskew=True, median=3, binarize="adaptive"),
    "G. D + 2x upscale": replace(PLAIN, deskew=True, median=3, upscale=2.0),
    "H. D + psm 6": replace(PLAIN, deskew=True, median=3, psm=6),
    "I. D + whitelist re-reads (production default)": replace(
        PLAIN, deskew=True, median=3, whitelist_rereads=True
    ),
    "J. A + whitelist re-reads": replace(PLAIN, dpi=400, median=3, whitelist_rereads=True),
    "K. 300 dpi + denoise + whitelist re-reads": replace(PLAIN, median=3, whitelist_rereads=True),
}


@dataclass(frozen=True)
class Token:
    """One thing the OCR text must contain exactly."""

    kind: str
    text: str
    phrase: bool = False


def key_tokens(truth: Truth) -> list[Token]:
    """The key tokens a perfect reading of this report contains."""
    v = truth.values
    day: date = v["biopsy_date"]
    tokens = [Token("procedure date", f"{day:%m/%d/%Y}")]
    if truth.organ == "heart":
        wording = (
            "No acute cellular rejection" if v["acr_grade"] == "0R" else "Acute cellular rejection"
        )
        return [
            *tokens,
            Token("ISHLT ACR grade", v["acr_grade"]),
            Token("pAMR grade", v["pamr_grade"], phrase=True),
            Token("diagnosis wording", wording, phrase=True),
        ]
    for field, label in SCORE_LABELS.items():
        if v.get(field) is not None:
            tokens.append(Token("Banff score", f"{label}{v[field]}"))
    tokens.append(Token("C4d", f"C4d{v['c4d']}"))
    if v.get("tcmr_grade"):
        tokens.append(Token("TCMR grade", v["tcmr_grade"]))
    if v.get("adequacy"):
        tokens.append(Token("adequacy", v["adequacy"]))
    tokens += [Token("diagnosis wording", w, phrase=True) for w in KIDNEY_WORDING[v["diagnosis"]]]
    if v.get("additional_finding"):
        tokens.append(
            Token("finding wording", FINDING_WORDING[v["additional_finding"]], phrase=True)
        )
    return tokens


def found(token: Token, words: set[str], text: str) -> bool:
    """Exact match: a whole word (outer punctuation ignored), or a phrase in the text."""
    return token.text.lower() in text if token.phrase else token.text in words


@dataclass(frozen=True)
class Scored:
    """Tokens found and missed in one report, and the OCR time."""

    biopsy_id: str
    hits: tuple[str, ...]
    misses: tuple[str, ...]
    seconds: float


def score(args: tuple[Truth, Path, OcrSettings]) -> Scored:
    """OCR one report and check its key tokens."""
    truth, path, settings = args
    started = time.monotonic()
    result: OcrResult = ocr_with_tesseract(path, settings)
    seconds = time.monotonic() - started
    words = {EDGE.sub("", w.text) for line in result.lines for w in line.words}
    text = " ".join(" ".join(line.text.split()) for line in result.lines).lower()
    hits: list[str] = []
    misses: list[str] = []
    for token in key_tokens(truth):
        (hits if found(token, words, text) else misses).append(f"{token.kind}|{token.text}")
    return Scored(truth.biopsy_id, tuple(hits), tuple(misses), seconds)


def render_sample(sample: Sequence[Truth], dpi: int, out: Path) -> Path:
    """Re-render the sampled reports at another dpi, exactly as the generator would."""
    config = replace(GeneratorConfig(), pdf_dpi=dpi)
    for truth in sample:
        index = patient_index(truth.biopsy_id)
        record = build_record(index, config, with_notes=False)
        center = next(c for c in config.centers if c.tenant_id == record.patient.tenant_id)
        n = next(i for i, b in enumerate(record.biopsies) if b.biopsy_id == truth.biopsy_id)
        write_report(
            record.biopsies[n],
            record.patient,
            center.name,
            out,
            dpi,
            rng_for(config.seed, STREAM_PDF, index, n),
        )
    return out


def measure(sample: Sequence[Truth], root: Path, settings: OcrSettings) -> list[Scored]:
    """Score every sampled report under one setup (in parallel processes)."""
    jobs = [(t, root / t.pdf_file, settings) for t in sample]
    with ProcessPoolExecutor(8) as pool:
        return list(pool.map(score, jobs))


def kind_of(token: str) -> str:
    return token.split("|")[0]


def summary_row(name: str, results: Sequence[Scored]) -> tuple[str, float]:
    """A markdown row (setup, accuracy, all-correct reports, seconds) and the accuracy."""
    hits = sum(len(r.hits) for r in results)
    total = hits + sum(len(r.misses) for r in results)
    perfect = sum(not r.misses for r in results)
    seconds = sum(r.seconds for r in results) / len(results)
    accuracy = hits / total
    return (
        f"| {name} | {hits}/{total} | **{100 * accuracy:.1f}%** | "
        f"{perfect}/{len(results)} | {seconds:.2f} |",
        accuracy,
    )


def kind_rows(name: str, results: Sequence[Scored]) -> list[str]:
    """Accuracy per token kind for one setup."""
    hit, miss = Counter[str](), Counter[str]()
    for r in results:
        hit.update(kind_of(t) for t in r.hits)
        miss.update(kind_of(t) for t in r.misses)
    return [
        f"| {name} | {kind} | {hit[kind]}/{hit[kind] + miss[kind]} | "
        f"{100 * hit[kind] / (hit[kind] + miss[kind]):.1f}% |"
        for kind in sorted(hit | miss)
    ]


def report(runs: dict[str, list[Scored]], best: str, sample_size: int) -> str:
    """The markdown report."""
    rows = [summary_row(name, results) for name, results in runs.items()]
    examples = Counter(t for r in runs[best] for t in r.misses).most_common(12)
    lines = [
        "# OCR of biopsy reports (no LLM)",
        "",
        f"Generated {date.today().isoformat()} by `make ocr-report` "
        "(evals/src/txlens_evals/ocr_benchmark.py). Synthetic data only.",
        "",
        f"**Best setup: {best} reads {100 * max(a for _, a in rows):.1f}% of key tokens exactly.**",
        "",
        f"{sample_size} reports (the extraction eval's stratified sample: ~60% kidney, ~40%",
        "heart). A key token counts when it appears exactly in the OCR text: a Banff score such",
        "as `t2`, C4d, the TCMR grade, adequacy, the procedure date, the ISHLT ACR and pAMR",
        "grades, and the exact diagnosis and finding wording.",
        "",
        "| Setup | Key tokens exact | Accuracy | Reports all exact | Seconds per report |",
        "|---|---|---|---|---|",
        *(row for row, _ in rows),
        "",
        "## By kind of token (best setup)",
        "",
        "| Setup | Kind | Exact | Accuracy |",
        "|---|---|---|---|",
        *kind_rows(best, runs[best]),
        "",
        "## Most frequent misses (best setup)",
        "",
        "| Token | Reports |",
        "|---|---|",
        *(f"| {token.replace('|', ': ')} | {count} |" for token, count in examples),
        "",
        "## Notes",
        "",
        "- `generated PDFs` are the files in data/raw (scanned at the generator's pdf_dpi, 200",
        "  since Step 2.1; 120 before, which read at most 72% with the best setup).",
        "  `re-rendered` rows read the same reports drawn at another scan resolution",
        "  (data/eval/), everything else equal.",
        '- Whitelist re-reads read tokens after known anchors ("scores:", "capillaries):",',
        '  "Banff grade", "ISHLT 2004):") a second time, allowing only the characters such a',
        "  token can contain. That uses knowledge of this report template; real reports would",
        "  need it per template.",
        '- Exact match is strict: "gO" for g0 is a miss here, although the evidence check of',
        "  the extraction tolerates it.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=Path("evals/reports/ocr.md"))
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE)
    parser.add_argument("--render-dpi", type=int, nargs="*", default=[])
    parser.add_argument("--setups", nargs="*", help="setup letters to run (default: all)")
    args = parser.parse_args(argv)
    truths = read_truth(args.data / "ground_truth" / "biopsies.csv")
    sample = stratified(truths, args.sample, SEED)
    chosen = {k: v for k, v in SETUPS.items() if not args.setups or k[0] in args.setups}
    runs: dict[str, list[Scored]] = {}
    for name, settings in chosen.items():
        runs[f"generated PDFs: {name}"] = measure(sample, args.data / "raw", settings)
        print(
            summary_row(f"generated PDFs: {name}", runs[f"generated PDFs: {name}"])[0], flush=True
        )
    for dpi in args.render_dpi:
        root = render_sample(sample, dpi, args.data / "eval" / f"pdf_{dpi}dpi")
        for name, settings in chosen.items():
            label = f"re-rendered at {dpi} dpi: {name}"
            runs[label] = measure(sample, root, settings)
            print(summary_row(label, runs[label])[0], flush=True)
    best = max(runs, key=lambda name: summary_row(name, runs[name])[1])
    args.out.write_text(report(runs, best, len(sample)), encoding="utf-8")
    print(f"Wrote {args.out}; best: {best}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
