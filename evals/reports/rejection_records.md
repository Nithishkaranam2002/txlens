# Rejection records: database vs ground truth

Generated 2026-11-12 by `make rejection-report` (evals/src/txlens_evals/rejection_records.py). Synthetic data only.

## Rejection conditions read from notes

| Records | Patients | Supported by a true rejection biopsy | Unsupported |
|---|---|---|---|
| 213 | 210 | 213 | 0 |

Supported: a true rejection biopsy 0-14 days before the record (the biopsy-result note). An unsupported record is a rejection dated where none happened, usually a past rejection read as current.

## Late-rejection cohorts (all rejection records)

| Rule | Expected | Actual | Missing | of which report in review | Extra | Result |
|---|---|---|---|---|---|---|
| rejection > 180 days after transplant | 160 | 129 | 31 | 31 | 0 | PASS once reviewed |
| rejection > 365 days after transplant | 39 | 33 | 6 | 6 | 0 | PASS once reviewed |

Extra patients would mean a rejection dated where none happened. A missing patient whose pathology report is in the review queue appears once a person approves it.
