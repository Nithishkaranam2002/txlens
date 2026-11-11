# Biopsy report extraction

Generated 2026-11-11 by `make biopsy-report` (evals/src/txlens_evals/biopsy_extraction.py). Synthetic data only.

**1508 reports (326 kidney, 1182 heart): 58.3% sent to human review; 716 of 716 auto-approved reports (QC sample included) fully correct; $0.0007 per report.**

## Run

| Setting | Value |
|---|---|
| Model | openai/gpt-6-luna (pass B: same model) |
| Prompt version | biopsy-2026-11-11.2 |
| OCR | tesseract |
| Review threshold | 0.7 |
| Budget batch | eval-full (cap $2.50, spent $0.9961) |
| QC sample | 10% of would-be auto-approved reports |
| Reports paid for in this run | 1408 (served from cache: 100) |
| Tokens per report | 5310 in, 793 out |
| Latency per report | p50 5.7 s, p95 11.8 s |
| Latency per LLM pass | p50 4.3 s, p95 10.0 s |
| Not processed (budget) | 0 |

## Accuracy per field

A value is correct when it equals the ground truth. The final value is counted, after
the evidence check: a value whose quote did not hold is empty and counts as wrong. For
fields some reports leave out, the last column counts how often the model correctly
returned nothing.

| Organ | Field | Reports | Correct | Accuracy | Not in report |
|---|---|---|---|---|---|
| kidney | biopsy_date | 326 | 321 | 98.5% |  |
| kidney | diagnosis | 326 | 326 | 100.0% |  |
| kidney | tcmr_grade | 326 | 326 | 100.0% |  |
| kidney | banff_i | 326 | 326 | 100.0% |  |
| kidney | banff_t | 326 | 326 | 100.0% |  |
| kidney | banff_v | 326 | 326 | 100.0% |  |
| kidney | banff_g | 326 | 326 | 100.0% |  |
| kidney | banff_ptc | 326 | 326 | 100.0% |  |
| kidney | banff_ci | 326 | 325 | 99.7% |  |
| kidney | banff_ct | 326 | 326 | 100.0% |  |
| kidney | banff_cv | 326 | 325 | 99.7% | 15/15 empty as expected |
| kidney | c4d | 326 | 326 | 100.0% |  |
| kidney | adequacy | 326 | 326 | 100.0% | 18/18 empty as expected |
| kidney | additional_finding | 326 | 326 | 100.0% |  |
| heart | biopsy_date | 1182 | 1178 | 99.7% |  |
| heart | diagnosis | 1182 | 1182 | 100.0% |  |
| heart | acr_grade | 1182 | 1182 | 100.0% |  |
| heart | pamr_grade | 1182 | 1182 | 100.0% |  |

## Diagnosis confusion matrix

| truth \ extracted | no_rejection | borderline | TCMR | ABMR | mixed | none |
|---|---|---|---|---|---|---|
| **no_rejection** | 1234 |  |  |  |  |  |
| **borderline** |  | 61 |  |  |  |  |
| **TCMR** |  |  | 107 |  |  |  |
| **ABMR** |  |  |  | 63 |  |  |
| **mixed** |  |  |  |  | 43 |  |

## Review routing

Fully correct = every key field (date, diagnosis, grade, scores) equals the truth.
Auto-approved reports go into OMOP without a person looking; the share of them that
is fully correct is the number that matters most.

| Route | Reports | Fully correct | Share |
|---|---|---|---|
| Auto-approved (loaded without review) | 629 | 629 | 100.0% |
| QC sample (would have been auto-approved) | 87 | 87 | 100.0% |
| Sent to human review | 792 | 782 | 98.7% |

Why reports were sent to review (a report can have several reasons):

| Reason | Reports |
|---|---|
| low OCR confidence | 790 |
| QC sample (random check of auto-approval) | 87 |
| the two passes disagree | 25 |
| evidence check failed | 24 |
| not linked to a biopsy | 10 |
| required field missing | 10 |

## Review threshold

Reports that would be auto-approved at other thresholds (only the confidence rule
changes; reports held for any other reason stay in review), and how many of them
have a wrong key field. The threshold in use is 0.70. It was
chosen on the first 100-report sample of synthetic reports and must be re-checked on
more data; the QC sample keeps measuring the auto-approve error rate.

| Threshold | Would be auto-approved | Share | With a wrong key field |
|---|---|---|---|
| 0.85 | 211 | 14.0% | 0 |
| 0.70 | 716 | 47.5% | 0 |
| 0.50 | 1142 | 75.7% | 0 |

## Cost

This batch spent **$0.9961** ($0.0007 per paid report).

Biopsies with a result on the timeline: **1498 of 1508** (99.3%).

## Limitations

- The reports are synthetic and rendered from one template with one font. Real
  pathology reports vary in layout, wording and abbreviations, so these numbers are
  an upper bound for template-like reports, not an estimate for real ones.
- The scans are simulated (rotation, blur, speckle, JPEG). Real scans and faxes are
  usually worse; OCR errors, not the LLM, cause most mistakes here.
- The ground truth comes from the same generator that wrote the reports, so a value
  printed on the page is right by construction.
- Both passes use the same model with different prompts. Their agreement is a useful
  signal but not independent evidence; a second model for pass B would be stronger.
- The sample is stratified to include rare classes, so its overall accuracy is not
  the accuracy over the full, mostly-normal set.

## How to re-run

`make up && make biopsy-report` (needs OPENAI_API_KEY and Tesseract). Answers are
cached by file hash and prompt version, so a re-run costs nothing.
