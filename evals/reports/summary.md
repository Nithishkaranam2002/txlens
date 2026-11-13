# Evaluation summary

Generated 2026-11-12 by `make evals` (evals/src/txlens_evals/summary.py). Synthetic data only.

95% bootstrap intervals in brackets: reports, notes or distinct texts resampled 1,000 times. Cost is per unit without the cache; latency is per report (one LLM call, OCR excluded) or per note (LLM only: its ~19 calls per note one after another).

| Task (units) | System | Main metric | Second metric | Sent to review | Third metric | Cost / unit | Latency / unit |
|---|---|---|---|---|---|---|---|
| Biopsy reports (1,508) | rules only | F1 99.9% (99.9%-100.0%) | fully correct 99.2% (98.7%-99.6%) | none | 12 wrong reports loaded unchecked | $0.00000 | 40 µs |
| Biopsy reports (1,508) | LLM only | F1 99.9% (99.9%-100.0%) | fully correct 99.2% (98.7%-99.6%) | none | 12 wrong reports loaded unchecked | $0.00048 | 4.3 s |
| Biopsy reports (1,508) | hybrid | F1 99.9% (99.9%-100.0%) | fully correct 99.3% (98.8%-99.7%) | 58.3% | 0 wrong reports loaded unchecked | $0.00095 | 4.3 s |
| Notes, held-out sample (300) | rules-only | F1 94.3% (94.2%-94.4%) | temporality 69.6% (69.1%-70.0%) | 0.0% (0.0%-0.0%) | concept 100.0% (100.0%-100.0%) | $0.00000 | 10.4 ms |
| Notes, held-out sample (300) | medspaCy | F1 94.3% (94.2%-94.4%) | temporality 100.0% (100.0%-100.0%) | 0.0% (0.0%-0.0%) | concept 100.0% (100.0%-100.0%) | $0.00000 | 12.3 ms |
| Notes, held-out sample (300) | LLM only | F1 84.0% (83.7%-84.2%) | temporality 85.2% (84.1%-86.3%) | 100.0% (100.0%-100.0%) | concept 71.5% (70.1%-73.0%) | $0.00163 | 41.8 s |
| Notes, held-out sample (300) | hybrid (+LLM) | F1 94.3% (94.2%-94.4%) | temporality 100.0% (100.0%-100.0%) | 0.0% (0.0%-0.0%) | concept 100.0% (100.0%-100.0%) | $0.00000 | 12.4 ms |
| Notes, all held-out (12,205) | rules-only | F1 94.2% (94.2%-94.2%) | temporality 69.8% (69.7%-69.9%) | 0.0% (0.0%-0.0%) | concept 100.0% (100.0%-100.0%) | $0.00000 | 10.4 ms |
| Notes, all held-out (12,205) | medspaCy | F1 94.2% (94.2%-94.2%) | temporality 100.0% (100.0%-100.0%) | 0.0% (0.0%-0.0%) | concept 100.0% (100.0%-100.0%) | $0.00000 | 11.7 ms |
| Notes, all held-out (12,205) | hybrid (+LLM) | F1 94.2% (94.2%-94.2%) | temporality 100.0% (100.0%-100.0%) | 0.0% (0.0%-0.0%) | concept 100.0% (100.0%-100.0%) | $0.00000 | 11.9 ms |
| Condition texts (2,234 coded rows) | vocabulary only | correct 60.2% (0.0%-98.4%) | cause in organ hierarchy 100.0% (100.0%-100.0%) | 39.8% (1.6%-100.0%) unmapped | | $0 | - |
| Condition texts (2,234 coded rows) | curated + vocabulary | correct 100.0% (100.0%-100.0%) | cause in organ hierarchy 100.0% (100.0%-100.0%) | 0.0% (0.0%-0.0%) unmapped | | $0 | - |

Details: [extraction_systems.md](extraction_systems.md), [notes_nlp.md](notes_nlp.md), [mapping_accuracy.md](mapping_accuracy.md).

## What the numbers do and do not say

- All data is synthetic and template-generated: these are upper bounds for template-like text, not estimates for real clinical documents.
- The intervals show sampling noise only. They do not cover the gap between these templates and real documents, which is much larger.
- Biopsy: on one template, rules match the LLM's accuracy. The hybrid's value is that no wrong report reaches OMOP unchecked.
- Notes: the LLM-only system reads every sentence, so every finding needs review (confidence 0.5) and it costs the most; rules carry the load.

## Survival analysis ([survival.md](survival.md))

- R parity: all quantities within tolerance (largest Cox coefficient difference 4e-06).
- Immortal time bias, simulated true HR 1: naive 0.56, landmark 0.99.
- ABMR in year 1 vs no rejection, from a 1-year landmark: HR 17.7 (9.3-33.4).
- 1 - KM overstates 3-year graft loss by 0.10 percentage points (main data).

## Rejection-risk model ([risk_model.md](risk_model.md))

- Trained on synthetic data, not clinically validated, decision support only.
- Champion `logistic_regression`, external test (center C, 4943 visits, 78 with rejection in 90 days): AUROC 0.804 (0.756 to 0.854), AUPRC 0.235 (0.154 to 0.361) against a prevalence of 0.016.
- At the rule engine's alert rate (7.7% of visits): model catches 0.436 (0.351 to 0.529) of rejection visits, rules 0.449 (0.371 to 0.539); difference -0.013 (-0.078 to 0.055).
- 1.4% of kept visits were really for-cause draws; without draws shortly before a biopsy the external AUROC is 0.744.

## Research copilot ([copilot.md](copilot.md))

From `make copilot-report`, which is not part of `make evals`: it asks the literature questions 3 times without the answer cache to measure run-to-run variance, so each run costs about $0.30.

- Data questions (40): execution accuracy 100.0% (100.0%-100.0%), tool choice 87.5% (77.5%-95.0%), structured tool where one fits 79.2% (62.5%-95.8%).
- Literature (60): faithfulness 0.91, answer relevancy 0.67 (RAGAS, judge `openai/gpt-5.6-luna`); citations supported 80.7% (75.0%-86.5%), supported or partly 99.5% (98.4%-100.0%); unanswerable declined 9 of 9.
- Red team: 25 of 25 attacks refused or neutralized.
- $0.0019 per question; latency median 8.6 s.
