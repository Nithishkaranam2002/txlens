# Condition mapping: free text vs the codes the centers sent

Generated 2026-11-12 by `make mapping-report` (evals/src/txlens_evals/mapping_accuracy.py). Synthetic data only.

2,234 coded FHIR Conditions (center_a: 1,705, center_c: 529), 19 distinct texts. The code is hidden, the text is mapped, and the result is compared with the code. 95% intervals resample distinct texts (1,000 times).

| System | Same concept: correct | Same concept: unmapped | Cause: in the organ's hierarchy | Cause: unmapped |
|---|---|---|---|---|
| vocabulary only | 60.2% (0.0%-98.4%) | 39.8% (1.6%-100.0%) | 100.0% (100.0%-100.0%) | 0.0% (0.0%-0.0%) |
| curated + vocabulary | 100.0% (100.0%-100.0%) | 0.0% (0.0%-0.0%) | 100.0% (100.0%-100.0%) | 0.0% (0.0%-0.0%) |

- Same concept: diabetes, hypertension and graft failure (correct = the code's concept or a more specific one). Cause: the primary-disease entry, coded as ESRD or heart failure but naming the cause (correct = within kidney or heart disease).
- Unmapped texts go to the review queue; they are never guessed.

## Pipeline matcher by method (rows)

| Method | exact | more specific | in hierarchy | wrong | unmapped |
|---|---|---|---|---|---|
| curated | 474 | 0 | 0 | 0 | 0 |
| exact | 718 | 0 | 1,042 | 0 | 0 |

## Wrong or unmapped texts (pipeline matcher)

| Code | Text | Rows | Mapped to | Outcome |
|---|---|---|---|---|

## Honest limits

- The texts come from one generator with a short list of diseases, and the curated rows were written knowing those texts: they are AI-curated and pending clinical review. The codes are an independent check of those rows, not of real problem lists.
- Center B sends no codes, so its rows cannot be scored this way; it uses the same texts as A and C.
