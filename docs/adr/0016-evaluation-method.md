# ADR 0016: How TxLens evaluates extraction, NLP and mapping

**Status:** accepted (2026-11-12)

## Context
Steps 2.1 to 2.3 each had their own report. Step 2.4 asks for one comparable view: per-field
precision, recall and F1, negation and concept accuracy, the share sent to review, and cost
and latency per document, for rules only, medspaCy, LLM only and hybrid. It must be
reproducible with one command, and every README number must come from it. The user also
asked for 95% confidence intervals, so the uncertainty from sample size is visible.

## Decision
- **One command, cached answers.** `make evals` reruns every evaluation that works on the
  loaded database, then builds `evals/reports/summary.md` from the JSON each script writes
  next to its report. The summary never parses markdown and never runs a model itself.
  LLM answers come from the cache, so a rerun costs nothing.
- **Biopsy reports: three systems on the same OCR text, with no new calls.**
  - *Rules only*: a regex extractor (`biopsy_rules.py`).
  - *LLM only*: the first pass's stored raw answer.
  - *Hybrid*: the pipeline's final machine values, before any human edit.
  - Fields are scored as slots: a wrong value counts as both a false positive and a false
    negative.
- **Notes: four systems on 300 held-out notes** (seed 11, never the tuning notes).
  - LLM only asks about every sentence. Its answers pass the same quote check, and our
    vocabulary still picks the concept, so it measures the model, not a looser pipeline.
  - The $1 cap was approved; the run spent $0.18.
  - Rules only, medspaCy and hybrid are also reported on all 12,205 held-out notes.
- **Mapping: the sent code as hidden truth.** Centers A and C attach SNOMED codes. The text
  is mapped without its code, and the result is compared with the code (or with the organ's
  disease hierarchy, where the text names the cause of ESRD or heart failure).
- **Intervals:** 95% percentile bootstrap, 1,000 resamples, fixed seed.
  - The units are whole reports, notes or distinct texts, which keeps the dependence
    between the fields of one unit.
  - Each resample is computed in one numpy matrix product.
- **The hybrid's routing is scored, not just its accuracy.** "Wrong reports loaded without
  review" counts what reaches OMOP unchecked: everything wrong for rules only and LLM only,
  and only what the hybrid did not send to review.

## Alternatives
- **Re-run every LLM system for the comparison.** It costs money and time, and gives
  different answers than the ones the pipeline actually stored. Scoring the stored answers
  evaluates what really happened.
- **Normal-approximation or Wilson intervals per metric.** These are simple for
  proportions, but wrong for F1 and for fields that are correlated within a document. The
  bootstrap handles both.
- **LLM only on every note** (about $20). The 300-note sample already gives an F1 interval
  of 0.5 points; more notes would not change the conclusion.
- **A hand-written expected concept per condition text.** The AI assistant that wrote the
  curated map would also be writing its answer key. The codes from the sending systems are
  independent of it.

## Consequences
- The headline is honest about what wins: on template text, rules equal the LLM, and the
  hybrid's value is routing (0 wrong reports reached OMOP unchecked, against 12 for each
  of the others).
- The intervals cover sampling noise only. The far larger gap between templates and real
  documents is stated in every report, not hidden in a number.
- Only 19 distinct condition texts are coded, so the mapping intervals are wide. That is
  the correct reading.
- Biopsy latency excludes OCR, which every system shares. The LLM-only note latency
  assumes its calls run one after another (this evaluation ran 8 in parallel).
