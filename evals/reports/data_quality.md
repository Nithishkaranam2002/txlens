# Data-quality report: medallion pipeline

Generated 2026-11-12 by `make pipeline-report` (evals/src/txlens_evals/pipeline_reconciliation.py). Synthetic data only.

**Reconciliation: 15 of 15 checks passed.**

## Reconciliation with the ground truth

| Check | Expected | Actual | Result | Note |
|---|---|---|---|---|
| patients: one person per ground-truth patient (MRN, per tenant) | 2,000 | 2,000 | PASS | missing 0, extra 0 |
| patients: sex, birth year (OMOP) and birth date (identity) | 2,000 | 2,000 | PASS | 0 differ |
| deaths: same people and dates | 65 | 65 | PASS | 0 differ |
| labs: every clean ground-truth result reaches silver | 240,945 | 240,945 | PASS |  |
| labs: patient, test, time and value (standard units) match | 240,945 | 240,945 | PASS | 1,186 censored results checked against their bound |
| labs: gold.measurement has one row per silver lab | 240,945 | 240,945 | PASS |  |
| medications: one drug_exposure per order | 22,234 | 22,234 | PASS | 0 orders missing |
| medications: normalized to the right RxNorm ingredient | 22,234 | 22,234 | PASS | 100.0% correct |
| medications: dose and start date | 22,234 | 22,234 | PASS |  |
| medications: continuous maintenance immunosuppression per the generator's protocol (active patients, transplant to extract 2026-09-30) | 1,853 | 1,853 | PASS | 0 patients with gaps (0 patient-drug gaps); see section below |
| duplicates: every injected duplicate removed, nothing else | 4,885 | 4,886 | PASS | missed 0; 1 extra, of which 1 are identical genuine results |
| quarantine: exactly the rows with an injected bad date or missing value | 3,686 | 3,686 | PASS | missed 0, extra 0 |
| notes: no identifier of the patient left in cdm.note text (gold) | 0 | 0 | PASS | 12,505 notes checked (MRN, names, birth date) |
| postgres: cdm + identity row counts equal gold, per tenant and table | 420,640 | 420,640 | PASS | 39 (tenant, table) pairs compared |
| notes: no identifier from app.patient_identity in cdm.note (Postgres) | 0 | 0 | PASS |  |

## Maintenance immunosuppression

Checked against the generator's protocol (tacrolimus + mycophenolate + prednisone for both organs). Real-world protocols vary, e.g. steroid-free or steroid-withdrawal regimens, belatacept or mTOR inhibitors, so this is a synthetic-data consistency check, not a clinical rule.

Every active patient is covered on every day.

## Rows per layer

| Layer | Table | Rows |
|---|---|---|
| bronze | lab_rows | 249,517 |
| bronze | fhir_resources | 44,656 |
| bronze | note_files | 12,505 |
| bronze | pdf_index | 3,335 |
| silver | patients | 2,000 |
| silver | encounters | 13,918 |
| silver | procedures | 3,508 |
| silver | conditions | 4,504 |
| silver | medications | 22,234 |
| silver | labs | 240,945 |
| silver | lab_duplicates | 4,886 |
| silver | notes | 12,505 |
| silver | quarantine | 3,686 |
| gold | person | 2,000 |
| gold | observation_period | 2,000 |
| gold | visit_occurrence | 13,918 |
| gold | condition_occurrence | 4,329 |
| gold | observation | 175 |
| gold | drug_exposure | 22,234 |
| gold | measurement | 240,945 |
| gold | procedure_occurrence | 3,508 |
| gold | death | 65 |
| gold | note | 12,505 |
| gold | cdm_source | 3 |
| gold | patient_identity | 2,000 |
| gold | note_phi_span | 116,958 |
| gold | lineage | 301,679 |
| gold | mapping_gaps | 8 |

## Quarantine reasons

| Entity | Tenant | Reason | Rows |
|---|---|---|---|
| labs | center_a | bad_date | 1,040 |
| labs | center_a | missing_value | 452 |
| labs | center_b | bad_date | 839 |
| labs | center_b | missing_value | 437 |
| labs | center_c | bad_date | 592 |
| labs | center_c | missing_value | 326 |

## Mapping coverage (rows with a standard concept)

| Table | Concept field | Tenant | Mapped | Total | Coverage |
|---|---|---|---|---|---|
| measurement | measurement_concept_id | center_a | 92,851 | 96,275 | 96.4% |
| measurement | measurement_concept_id | center_b | 80,243 | 83,213 | 96.4% |
| measurement | measurement_concept_id | center_c | 59,245 | 61,457 | 96.4% |
| measurement | unit_concept_id | center_a | 92,851 | 96,275 | 96.4% |
| measurement | unit_concept_id | center_b | 80,243 | 83,213 | 96.4% |
| measurement | unit_concept_id | center_c | 59,245 | 61,457 | 96.4% |
| drug_exposure | drug_concept_id | center_a | 8,873 | 8,873 | 100.0% |
| drug_exposure | drug_concept_id | center_b | 7,666 | 7,666 | 100.0% |
| drug_exposure | drug_concept_id | center_c | 5,695 | 5,695 | 100.0% |
| drug_exposure | route_concept_id | center_a | 8,873 | 8,873 | 100.0% |
| drug_exposure | route_concept_id | center_b | 7,666 | 7,666 | 100.0% |
| drug_exposure | route_concept_id | center_c | 5,695 | 5,695 | 100.0% |
| condition_occurrence | condition_concept_id | center_a | 1,705 | 1,705 | 100.0% |
| condition_occurrence | condition_concept_id | center_b | 1,512 | 1,512 | 100.0% |
| condition_occurrence | condition_concept_id | center_c | 1,112 | 1,112 | 100.0% |
| procedure_occurrence | procedure_concept_id | center_a | 1,414 | 1,414 | 100.0% |
| procedure_occurrence | procedure_concept_id | center_b | 1,188 | 1,188 | 100.0% |
| procedure_occurrence | procedure_concept_id | center_c | 906 | 906 | 100.0% |
| measurement | measurement_concept_id | **all** | 232,339 | 240,945 | **96.4%** |
| measurement | unit_concept_id | **all** | 232,339 | 240,945 | **96.4%** |
| drug_exposure | drug_concept_id | **all** | 22,234 | 22,234 | **100.0%** |
| drug_exposure | route_concept_id | **all** | 22,234 | 22,234 | **100.0%** |
| condition_occurrence | condition_concept_id | **all** | 4,329 | 4,329 | **100.0%** |
| procedure_occurrence | procedure_concept_id | **all** | 3,508 | 3,508 | **100.0%** |

## Free-text conditions (Step 2.2)

Every FHIR Condition, whichever OMOP table its concept's domain sends it to. "Codes only" is what the pipeline mapped before Step 2.2; text matching adds the curated map (TXLENS_CONDITION rows: curated by AI assistant, pending clinical review), exact name or synonym, normalized and fuzzy matches (fuzzy accepted at >= 0.90 and only when type, acuity, side, grade, stage and numbers match exactly). Unmapped texts and their best suggestion are in `evals/reports/condition_mapping_review.csv`.

| Tenant | Conditions | Codes only | Coverage before | Mapped now | Coverage now | -> condition_occurrence | -> observation |
|---|---|---|---|---|---|---|---|
| center_a | 1,780 | 1,705 | 95.8% | 1,780 | 100.0% | 1,705 | 75 |
| center_b | 1,569 | 0 | 0.0% | 1,569 | 100.0% | 1,512 | 57 |
| center_c | 1,155 | 529 | 45.8% | 1,155 | 100.0% | 1,112 | 43 |
| **all** | 4,504 | 2,234 | 49.6% | 4,504 | 100.0% | 4,329 | 175 |

Rows by mapping method:

| Tenant | code | curated | exact | normalized | fuzzy | unmapped |
|---|---|---|---|---|---|---|
| center_a | 1,705 | 75 | 0 | 0 | 0 | 0 |
| center_b | 0 | 378 | 1,191 | 0 | 0 | 0 |
| center_c | 529 | 163 | 463 | 0 | 0 | 0 |
| **all** | 2,234 | 616 | 1,654 | 0 | 0 | 0 |

## Mapping gaps (review queue, largest first)

| Tenant | Table | Field | Source value | Reason | Rows |
|---|---|---|---|---|---|
| center_a | measurement | unit_concept_id | MFI | no_standard_unit | 3,424 |
| center_b | measurement | unit_concept_id | MFI | no_standard_unit | 2,970 |
| center_a | measurement | measurement_concept_id | DSA MAX MFI | no_standard_concept | 2,904 |
| center_b | measurement | measurement_concept_id | DSA (max MFI) | no_standard_concept | 2,513 |
| center_c | measurement | measurement_concept_id | DSAMFI | no_standard_concept | 2,212 |
| center_c | measurement | unit_concept_id | MFI | no_standard_unit | 2,212 |
| center_a | measurement | measurement_concept_id | DSA MFI | no_standard_concept | 520 |
| center_b | measurement | measurement_concept_id | DSA max | no_standard_concept | 457 |
