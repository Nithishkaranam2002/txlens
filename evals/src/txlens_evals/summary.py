"""summary.md: the headline numbers of every evaluation, with their 95% intervals.

Built only from the JSON files the evaluation scripts write next to their reports
(extraction_systems.json, notes_nlp.json, mapping_accuracy.json), so every number here comes
from a script anyone can re-run: `make evals` runs them all and then this.
"""

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

REPORTS = Path("evals/reports")
OUT = REPORTS / "summary.md"
NOTES_SAMPLE = "held-out sample"
NOTES_HELD_OUT = "held-out"
# Reading order: cheapest to most complex, then the pipeline's own system last.
ORDER = ("rules only", "rules-only", "medspaCy", "vocabulary only", "LLM only", "hybrid",
         "hybrid (+LLM)", "curated + vocabulary")  # fmt: skip


def ordered(systems: dict[str, Any]) -> list[tuple[str, Any]]:
    return sorted(systems.items(), key=lambda kv: ORDER.index(kv[0]) if kv[0] in ORDER else 99)


def ci(metric: dict[str, float] | None) -> str:
    """'94.2% (93.8%-94.6%)' from a stored estimate."""
    if not metric:
        return "-"
    return f"{metric['value']:.1%} ({metric['low']:.1%}-{metric['high']:.1%})"


def money(value: float | None) -> str:
    return "-" if value is None else f"${value:.5f}"


def latency(ms: float | None) -> str:
    if ms is None:
        return "-"
    if ms >= 1000:
        return f"{ms / 1000:.1f} s"
    return f"{ms:.1f} ms" if ms >= 1 else f"{ms * 1000:.0f} µs"


def load(name: str, folder: Path) -> dict[str, Any] | None:
    path = folder / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def biopsy_rows(data: dict[str, Any]) -> list[str]:
    lines = []
    for system, m in ordered(data["systems"]):
        review = f"{m['review_share']:.1%}" if system == "hybrid" else "none"
        cost, time = money(m["cost_per_report"]), latency(m["latency_p50_ms"])
        lines.append(f"| Biopsy reports ({data['reports']:,}) | {system} | F1 {ci(m['f1'])} | "
                     f"fully correct {ci(m['fully_correct'])} | {review} | "
                     f"{m['wrong_loaded']:,} wrong reports loaded unchecked | "
                     f"{cost} | {time} |")  # fmt: skip
    return lines


def note_rows(data: dict[str, Any], split: str, label: str) -> list[str]:
    part = data.get(split)
    if not part:
        return []
    lines = []
    for system, m in ordered(part["systems"]):
        lines.append(f"| {label} ({part['notes']:,}) | {system} | F1 {ci(m['f1'])} | "
                     f"temporality {ci(m['temporality'])} | {ci(m['review_share'])} | "
                     f"concept {ci(m['concept'])} | {money(m['cost_per_note'])} | "
                     f"{latency(m['latency_ms_per_note'])} |")  # fmt: skip
    return lines


def mapping_rows(data: dict[str, Any]) -> list[str]:
    return [
        f"| Condition texts ({data['coded_rows']:,} coded rows) | {system} | "
        f"correct {ci(m['same_concept_correct'])} | cause in organ hierarchy "
        f"{ci(m['cause_in_hierarchy'])} | {ci(m['unmapped'])} unmapped | | $0 | - |"
        for system, m in ordered(data["systems"])
    ]


def render(folder: Path) -> str:
    biopsy = load("extraction_systems.json", folder)
    notes = load("notes_nlp.json", folder)
    mapping = load("mapping_accuracy.json", folder)
    rows = [
        *(biopsy_rows(biopsy) if biopsy else []),
        *(note_rows(notes, NOTES_SAMPLE, "Notes, held-out sample") if notes else []),
        *(note_rows(notes, NOTES_HELD_OUT, "Notes, all held-out") if notes else []),
        *(mapping_rows(mapping) if mapping else []),
    ]
    missing = [n for n, d in (("extraction_systems", biopsy), ("notes_nlp", notes),
                              ("mapping_accuracy", mapping)) if d is None]  # fmt: skip
    return "\n".join([
        "# Evaluation summary",
        "",
        f"Generated {date.today().isoformat()} by `make evals` "
        "(evals/src/txlens_evals/summary.py). Synthetic data only.",
        "",
        "95% bootstrap intervals in brackets: reports, notes or distinct texts resampled "
        "1,000 times. Cost is per unit without the cache; latency is per report (one LLM "
        "call, OCR excluded) or per note (LLM only: its ~19 calls per note one after "
        "another).",
        "",
        "| Task (units) | System | Main metric | Second metric | Sent to review | Third "
        "metric | Cost / unit | Latency / unit |",
        "|---|---|---|---|---|---|---|---|",
        *rows,
        "",
        *([f"Missing (not run yet): {', '.join(missing)}.", ""] if missing else []),
        "Details: [extraction_systems.md](extraction_systems.md), [notes_nlp.md](notes_nlp.md), "
        "[mapping_accuracy.md](mapping_accuracy.md).",
        "",
        "## What the numbers do and do not say",
        "",
        "- All data is synthetic and template-generated: these are upper bounds for "
        "template-like text, not estimates for real clinical documents.",
        "- The intervals show sampling noise only. They do not cover the gap between these "
        "templates and real documents, which is much larger.",
        "- Biopsy: on one template, rules match the LLM's accuracy. The hybrid's value is that "
        "no wrong report reaches OMOP unchecked.",
        "- Notes: the LLM-only system reads every sentence, so every finding needs review "
        "(confidence 0.5) and it costs the most; rules carry the load.",
        "",
    ])  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--reports", type=Path, default=REPORTS)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    args.out.write_text(render(args.reports), encoding="utf-8")
    print(args.out.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
