# Biopsy extraction: rules only vs LLM only vs hybrid

Generated 2026-11-12 by `make extraction-systems` (evals/src/txlens_evals/biopsy_systems.py). Synthetic data only.

1,508 scanned reports (326 kidney, 1,182 heart), the same OCR text for every system. 95% bootstrap intervals in brackets (reports resampled 1,000 times). No model was called: the LLM answers are the stored ones.

## Headline

| System | Precision | Recall | F1 | Reports fully correct | Diagnosis F1 | Sent to review | Wrong reports loaded without review | LLM cost / report | Latency (p50) |
|---|---|---|---|---|---|---|---|---|---|
| rules only | 100.0% (100.0%-100.0%) | 99.9% (99.8%-99.9%) | 99.9% (99.9%-100.0%) | 99.2% (98.7%-99.6%) | 100.0% (100.0%-100.0%) | none (no routing) | 12 | $0.00000 | 40 µs |
| LLM only | 100.0% (100.0%-100.0%) | 99.9% (99.8%-99.9%) | 99.9% (99.9%-100.0%) | 99.2% (98.7%-99.6%) | 100.0% (100.0%-100.0%) | none (no routing) | 12 | $0.00048 | 4.3 s |
| hybrid | 100.0% (100.0%-100.0%) | 99.9% (99.8%-99.9%) | 99.9% (99.9%-100.0%) | 99.3% (98.8%-99.7%) | 100.0% (100.0%-100.0%) | 58.3% | 0 | $0.00095 | 4.3 s |

- Fully correct: every field of the report equals the truth.
- Sent to review: only the hybrid routes reports to a person; the other two would load every answer as it is. Wrong reports loaded without review is the number that reaches OMOP unchecked: for the hybrid, wrong reports it did not send to review.
- Latency: rules only is the extraction itself; LLM latency is per paid call (the hybrid makes two in parallel); OCR, the same for all, is left out.

## Per field (precision / recall / F1)

| Organ | Field | rules only: P / R / F1 | LLM only: P / R / F1 | hybrid: P / R / F1 |
|---|---|---|---|---|
| kidney | biopsy_date | 100.0% / 98.5% / 99.2% | 100.0% / 98.5% / 99.2% | 100.0% / 98.5% / 99.2% |
| kidney | diagnosis | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% |
| kidney | tcmr_grade | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% |
| kidney | banff_i | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% |
| kidney | banff_t | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% |
| kidney | banff_v | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% |
| kidney | banff_g | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% |
| kidney | banff_ptc | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% |
| kidney | banff_ci | 100.0% / 99.7% / 99.8% | 100.0% / 99.4% / 99.7% | 100.0% / 99.7% / 99.8% |
| kidney | banff_ct | 100.0% / 99.7% / 99.8% | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% |
| kidney | banff_cv | 100.0% / 99.7% / 99.8% | 100.0% / 99.7% / 99.8% | 100.0% / 99.7% / 99.8% |
| kidney | c4d | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% |
| kidney | adequacy | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% |
| kidney | additional_finding | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% |
| heart | biopsy_date | 100.0% / 99.7% / 99.8% | 100.0% / 99.7% / 99.8% | 100.0% / 99.7% / 99.8% |
| heart | diagnosis | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% |
| heart | acr_grade | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% |
| heart | pamr_grade | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% | 100.0% / 100.0% / 100.0% |

## Honest limits

- One generator draws every report from one template, so the rules know every label. On real reports with other layouts the rules would fail first; the LLM would degrade more gracefully. The gap here is a lower bound on the LLM's value.
- Most remaining errors come from OCR (simulated scans), not from either reader.
- The hybrid's extra value is less in its accuracy than in its routing: what it is unsure of goes to a person instead of into OMOP.
