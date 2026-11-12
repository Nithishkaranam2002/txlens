# ADR 0017: Survival analysis, its R reference, and how bias and small cells are handled

**Status:** accepted (2026-11-12)

## Context
Step 3.1 adds research analytics on OMOP data:
- Kaplan-Meier graft survival with a log-rank test;
- competing risks (death with a working graft competes with graft loss);
- a Cox model with a proportional-hazards check;
- landmark analysis for groups that are only known during follow-up.

Researchers and clinicians use it through the API. The output can be exported, so it must
be aggregate and small-cell safe for every role. Researchers trust R's `survival` package;
the app runs Python. Delayed graft function (DGF), one of the Cox covariates, existed only
in clinic notes.

## Decision
- **DGF in OMOP: the "History of event" pattern.**
  - DGF (SNOMED 426136000, concept 4138908) is a standard concept in the Condition domain,
    which `make vocab-check` now verifies.
  - Clinic notes mention it as history ("history of DGF"): a fact recorded at the note date
    about an undated episode around transplant.
  - OMOP's convention for that is an observation with `observation_concept_id` 1340204
    ("History of event") and `value_as_concept_id` = the condition, dated when recorded.
    That is the same pattern as "history of kidney transplant" from the problem list
    (Step 2.2).
  - The other option, a `condition_occurrence` dated at the transplant, would invent an
    onset date the note does not give.
  - The rule is generic: every affirmed, patient-experienced past condition mention with a
    Condition-domain concept becomes such an observation (migration 0015). The
    `note_cdm_row` link keeps the source sentence.
- **R is the reference; the port matches R's defaults.**
  - `r_parity/R/survival_reference.R` uses survival 3.7.0 and cmprsk 2.2.12. It runs in a
    Docker image pinned by digest, on a dated CRAN snapshot. There is no R on the host and
    none on Databricks.
  - A parity test feeds R and Python the same CSV extract (a simulated one, and one read back
    through the OMOP extract SQL) and asserts tolerances. It runs in its own CI job.
  - Where the libraries differ, the port follows R:
    - Efron ties;
    - Greenwood SE on the log scale with a "log" CI;
    - Aalen-Johansen without jitter, with infinitesimal-jackknife SEs;
    - Cox convergence precision 1e-10 with a 0.5 first step.

    Every difference is listed in `r_parity/README.md`.
- **Aalen-Johansen is our own numpy code**, because lifelines jitters tied times. It matches
  R to about 1e-16, standard errors included.
- **Immortal time is prevented by construction, not by documentation.**
  - Every grouping has a horizon, the day it is known: DGF day 7, the month-1 DSA test
    day 30, year-1 dd-cfDNA or rejection day 365. A saved cohort's horizon is the end of its
    latest lab window.
  - The analysis starts at that landmark by default; an earlier landmark is refused with
    422.
  - Cohort membership is re-checked with only what was known by the landmark (conditions
    recorded by then).
  - The Cox model starts at its covariates' horizon (day 30, because DSA is a month-1
    test). It uses complete cases, and covariates that become constant are dropped with a
    warning (heart grafts have no DSA or DGF).
- **Small cells, for every role.**
  - Counts of 1-10 are shown as "<11".
  - Curve steps are pooled so that each displayed drop covers at least 11 events. The
    estimate shown is the true one at that time.
  - Curves stop before fewer than 11 patients are at risk; a group with fewer than 11 events
    gets no curve.
  - "At 1/2/3 years" values are the displayed curve's last step, given with that step's
    day, so they reveal nothing the curve does not.
  - CSV and PDF are built from the suppressed result only.
- **The methods paragraph is a template filled from the analysis**, not LLM text.
- **Known effects for testing come from a simulator, not the generator.** The data generator
  draws outcomes as flat probabilities, so donor type, age and DGF truly have no effect there.
  `survival/simulate.py` builds an extract with known hazard ratios, ties, competing deaths
  and a null dd-cfDNA exposure. The generator is unchanged.

## Alternatives
- **Call R from the API (rpy2 or Plumber).** That is one source of truth, but it means
  shipping R in production, which Databricks Free Edition jobs cannot run. Parity tests give
  the same assurance with Python only at runtime.
- **statsmodels `PHReg` for Cox.** Its default is Breslow ties, which with whole-day times
  gives coefficients different from R's.
- **lifelines `AalenJohansenFitter`.** It jitters ties, and its delta-method variance
  differs from R's.
- **Suppress only researchers' output** (the cohort pattern). Exports leave the system, and
  an aggregate report has no reason to show 1-10 to anyone.
- **Hide small values by rounding** (e.g. to the nearest 5). Differences between rounded
  steps can still reveal small counts; pooling steps avoids that.
- **Give the generator real hazard effects.** That would mean regenerating all data and
  re-baselining every Phase 1-2 report, for a check the simulator gives more precisely.

## Consequences
- R and Python agree to rounding for KM, log-rank and cumulative incidence, and to ~1e-6
  for Cox (see `evals/reports/survival.md`).
- Schoenfeld p-values differ by method (R's score test vs lifelines' approximation). They
  are reported side by side, not asserted.
- Per center, events are few (about 30-40 graft losses), so suppression leaves curves with
  only a few steps and often hides small groups. That is the intended trade-off.
- The main data's Cox hazard ratios are near 1 except DSA, which acts through rejection.
  The report explains this rather than tuning the data.
- Because DSA is a month-1 test, models that include it start at day 30. Earlier graft
  losses are outside them by design.
