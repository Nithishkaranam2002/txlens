# ADR 0003: Generate synthetic data "truth first", with per-patient random streams

**Status:** accepted (2026-11-10)

## Context
Every later step (pipelines, OMOP mapping, NLP, OCR, surveillance, copilot) needs messy input
data and a known correct answer to score against. Real patient data is not allowed. The data
must be reproducible, small enough for a laptop, and large enough (2,000 patients by default)
to give enough rejection and graft-loss events for survival analysis.

## Decision
- First simulate each patient's true story in memory (demographics, episodes, lab signals,
  outcomes, meds, biopsies, notes). Then render that same story twice: as messy raw files
  (`data/raw/`) and as clean answers (`data/ground_truth/`). Raw and truth cannot disagree
  because they come from the same objects.
- Give each patient its own random stream from `SeedSequence(seed, spawn_key=(patient, part))`.
  A patient is identical whether 50 or 20,000 patients are generated, and changing one part
  (for example notes) does not shift the random numbers of another (for example labs).
- Stream patients one at a time to open files instead of holding the cohort in memory.
- Record every injected problem (duplicate, bad date, name variant, ...) with its file and row.

## Alternatives
- Synthea: realistic general EHR data, but no transplant-specific signals (dd-cfDNA, Banff
  biopsy grades, rejection timing) and no control over the "mess" we need to test.
- One global random stream: simpler, but adding one patient or one field changes everything
  after it, which makes test failures hard to read.
- Generate raw files first and parse them for truth: the truth would inherit parser bugs.

## Consequences
- Rates and ranges live in one config file and are covered by statistical tests.
- The scale run (20,000 patients, `make generate-data-scale`) uses ~50 MB of memory.
- The simulation is simplified on purpose (for example, at most one early and one late
  rejection episode). It is realistic enough to test software, not to study medicine.
