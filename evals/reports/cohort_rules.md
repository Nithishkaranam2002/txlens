# Cohort rules: database vs ground truth

Generated 2026-11-11 by `make cohort-report` (evals/src/txlens_evals/cohort_reconciliation.py). Synthetic data only.

Each rule runs through the API's cohort service on the pipeline-loaded database (2,000 patients, all centers, Row-Level Security on). Members are matched to the ground truth by (center, MRN).

**8 of 8 rules match the ground truth exactly.**

| Rule | Expected | Actual | Missing | Extra | Result |
|---|---|---|---|---|---|
| kidney, dd-cfDNA >= 1% within 365 days of transplant | 198 | 198 | 0 | 0 | PASS |
| any organ, dd-cfDNA >= 1% within 365 days of transplant | 204 | 204 | 0 | 0 | PASS |
| kidney, living donor | 620 | 620 | 0 | 0 | PASS |
| kidney, deceased donor | 1,082 | 1,082 | 0 | 0 | PASS |
| heart | 298 | 298 | 0 | 0 | PASS |
| age at transplant 40-59 | 715 | 715 | 0 | 0 | PASS |
| transplant in 2024 (whole year) | 522 | 522 | 0 | 0 | PASS |
| any diabetes mellitus (73211009 and descendants) | 820 | 820 | 0 | 0 | PASS |
