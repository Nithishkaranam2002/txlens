# Survival analysis: R parity, immortal time, competing risks

Generated 2026-11-12 by `make survival-report` (evals/src/txlens_evals/survival.py). Synthetic data only. Counts of 1-10 are shown as "<11", as in the app.

## 1. R vs Python parity

R 4.4.2 (2024-10-31), survival 3.7.0, cmprsk 2.2.12 (Docker image `txlens-r:local`) vs the Python port. Simulated: 3,000 patients. OMOP: the 2,000 pipeline-loaded patients.

| Quantity | Values compared (sim / OMOP) | Max abs. difference, simulated | Max abs. difference, OMOP | Tolerance | Within |
|---|---|---|---|---|---|
| km_n_risk | 1,707 / 303 | 0.0e+00 | 0.0e+00 | rel <= 0 | yes |
| km_estimate | 1,707 / 303 | 5.9e-15 | 5.4e-15 | abs <= 1e-10 | yes |
| km_se | 1,707 / 303 | 5.0e-17 | 4.9e-17 | abs <= 1e-10 | yes |
| km_ci | 3,414 / 606 | 6.1e-15 | 5.8e-15 | abs <= 1e-10 | yes |
| logrank_chisq | 1 / 1 | 4.1e-14 | 1.8e-14 | rel <= 1e-08 | yes |
| cif_estimate | 1,707 / 303 | 5.6e-16 | 5.9e-17 | abs <= 1e-10 | yes |
| cif_se | 1,707 / 303 | 1.0e-16 | 8.1e-17 | abs <= 1e-10 | yes |
| cif_ci | 3,414 / 606 | 7.2e-16 | 2.2e-16 | abs <= 1e-10 | yes |
| cox_coef | 7 / 7 | 4.3e-10 | 4.0e-06 | abs <= 1e-05 | yes |
| cox_se | 7 / 7 | 1.9e-11 | 4.3e-07 | rel <= 0.0001 | yes |
| immortal_coef | 3 / 3 | 8.1e-07 | 2.0e-06 | abs <= 1e-05 | yes |
| immortal_se | 3 / 3 | 4.7e-09 | 3.6e-08 | rel <= 0.0001 | yes |

**Proportional-hazards tests** (reported, not asserted: R's `cox.zph` uses a score test since survival 3.0, lifelines the older approximation). Same verdict at p < 0.05 for 14 of 14 covariate tests.

| Extract | Model | Covariate | R cox.zph p | lifelines p |
|---|---|---|---|---|
| simulated | full | age_per_decade | 0.052 | 0.051 |
| simulated | full | deceased_donor | 0.270 | 0.373 |
| simulated | full | dsa_positive | 0.505 | 0.582 |
| simulated | full | dgf | 0.353 | 0.459 |
| simulated | all_organs | age_per_decade | 0.129 | 0.123 |
| simulated | all_organs | deceased_donor | 0.098 | 0.121 |
| simulated | all_organs | heart | 0.459 | 0.709 |
| OMOP | full | age_per_decade | 0.863 | 0.918 |
| OMOP | full | deceased_donor | 0.971 | 0.801 |
| OMOP | full | dsa_positive | 0.127 | 0.127 |
| OMOP | full | dgf | 0.550 | 0.530 |
| OMOP | all_organs | age_per_decade | 0.865 | 0.829 |
| OMOP | all_organs | deceased_donor | 0.643 | 0.992 |
| OMOP | all_organs | heart | 0.112 | 0.122 |

Gray's test (R only, simulated, donor type): graft loss p = 0.00011, death p = 0.55. Library differences: [r_parity/README.md](../../r_parity/README.md).

## 2. Known hazard ratios recovered (simulated extract)

Simulated extract (3,000 patients). The kidney model starts at the month-1 DSA test (2,504 patients, 203 graft losses); heart grafts have no DSA or DGF, so the heart effect comes from a second model on every patient.

| Model | Covariate | True HR | Estimated HR (95% CI) | Truth inside CI |
|---|---|---|---|---|
| kidney model, from day 30 | Age at transplant (per 10 years) | 1.1 | 1.15 (1.03-1.28) | yes |
| kidney model, from day 30 | Deceased donor (vs living) | 1.5 | 1.73 (1.23-2.43) | yes |
| kidney model, from day 30 | DSA positive at month-1 test | 2.5 | 2.65 (1.86-3.77) | yes |
| kidney model, from day 30 | Delayed graft function | 1.8 | 1.71 (1.25-2.33) | yes |
| all organs | Heart graft (vs kidney) | 1.3 | 1.05 (0.77-1.43) | yes |

## 3. Immortal time bias: naive vs landmark vs time-varying

Exposure: **dd-cfDNA >= 1% at any test in year 1**. A patient can only have that result while still followed with a working graft, so comparing "ever high in year 1" with "never" from transplant gives the exposed group event-free time for free.

**Simulated, true HR = 1** (50 datasets of 3,000 patients; the test result is random and unrelated to the outcome):

| Design | Mean HR (geometric) | 95% CI excluded 1 in | Verdict |
|---|---|---|---|
| naive (from transplant) | 0.56 | 100% of datasets | biased (looks protective) |
| landmark at day 365 | 0.99 | 4% of datasets | unbiased |
| time-varying covariate | 1.01 | 6% of datasets | unbiased |

With no true effect, a correct design should exclude 1 in about 5% of datasets.

**Main data** (2,000 patients). Here high dd-cfDNA marks rejection, which the generator links to later graft loss, so there is a real effect:

| Design | HR (95% CI) | Patients | Graft losses |
|---|---|---|---|
| naive (from transplant) | 7.63 (5.07-11.48) | 2,000 | 95 |
| landmark at day 365 | 8.57 (5.20-14.12) | 1,812 | 63 |
| time-varying covariate | 9.64 (6.37-14.59) | 2,000 | 95 |

The naive design gives the smallest hazard ratio: the exposed group's early, immortal follow-up dilutes a real harm. The landmark and time-varying designs both give a larger effect.

## 4. Graft survival by rejection in year 1 (landmark at 1 year)

Landmark at day 365: 1,812 patients still with a working graft and followed. Groups use only biopsies by day 365.

| Group | Patients | Graft losses after day 365 | 2-year graft survival from the landmark (KM) | HR vs no rejection (95% CI) |
|---|---|---|---|---|
| ABMR in year 1 | 48 | 13 | 68.4% | 17.66 (9.32-33.44) |
| No rejection in year 1 | 1655 | 35 | 97.1% | reference |
| Other rejection in year 1 | 109 | 15 | 81.4% | 7.59 (4.15-13.91) |

Log-rank chi-square 161.8 on 2 df, p = 7.2e-36. This is the effect the generator does build in: an ABMR episode that does not resolve leads to graft loss 2 to 18 months after the biopsy.

## 5. Competing risks: cumulative incidence vs 1 - Kaplan-Meier

1 - Kaplan-Meier treats a patient who died with a working graft as censored: as if they were still at risk of graft loss later. They are not, so 1 - KM estimates graft loss in a world without death and overstates the real risk. Aalen-Johansen adds graft-loss risk only while the patient is alive with a working graft.

| Extract | Year | Cumulative incidence (Aalen-Johansen) | 1 - KM | 1 - KM overstates by |
|---|---|---|---|---|
| main data | 1 | 1.62% | 1.63% | +0.01 pts (+1%) |
| main data | 2 | 4.44% | 4.50% | +0.06 pts (+1%) |
| main data | 3 | 6.01% | 6.11% | +0.10 pts (+2%) |
| simulated | 1 | 6.25% | 6.30% | +0.05 pts (+1%) |
| simulated | 2 | 9.65% | 9.80% | +0.15 pts (+2%) |
| simulated | 3 | 12.81% | 13.14% | +0.33 pts (+3%) |

The main data has 52 deaths with a functioning graft against 95 graft losses, mostly late, so the gap is small; it grows with follow-up and with the death rate (the simulated cohort has more deaths).

## 6. Cox model on the main data

| Model | Covariate | HR (95% CI) | PH test p |
|---|---|---|---|
| kidney, from day 30 (1,682 patients, 82 graft losses) | Age at transplant (per 10 years) | 1.04 (0.91-1.19) | 0.92 |
| kidney, from day 30 (1,682 patients, 82 graft losses) | Deceased donor (vs living) | 0.88 (0.55-1.41) | 0.80 |
| kidney, from day 30 (1,682 patients, 82 graft losses) | DSA positive at month-1 test | 3.44 (1.77-6.68) | 0.13 |
| kidney, from day 30 (1,682 patients, 82 graft losses) | Delayed graft function | 1.15 (0.64-2.04) | 0.53 |
| all organs (2,000 patients, 95 graft losses) | Age at transplant (per 10 years) | 1.03 (0.91-1.17) | 0.83 |
| all organs (2,000 patients, 95 graft losses) | Deceased donor (vs living) | 0.93 (0.60-1.44) | 0.99 |
| all organs (2,000 patients, 95 graft losses) | Heart graft (vs kidney) | 0.84 (0.45-1.57) | 0.12 |

Intervals that include 1: Age at transplant (per 10 years); Deceased donor (vs living); Delayed graft function; Heart graft (vs kidney). That is the right answer for this data: the generator draws graft loss and death as flat probabilities that do not depend on age, donor type, organ or DGF.

Intervals that exclude 1: DSA positive at month-1 test. The generator gives DSA-positive patients more antibody-mediated rejection, and unresolved rejection leads to graft loss, so a positive month-1 DSA test does predict graft loss here (through rejection, not as a direct effect).

The method recovers real effects on the simulated extract (section 2).

## 7. Delayed graft function from clinic notes

DGF reaches OMOP as a "History of event" observation (concept 1340204) valued with SNOMED 426136000, from affirmed past mentions in clinic notes (ADR 0017). Kidney grafts, per patient:

| Metric | Value (exact 95% CI) | Counts |
|---|---|---|
| Sensitivity | 100.0% (98.8%-100.0%) | 304 of 304 |
| Specificity | 100.0% (99.7%-100.0%) | 1398 of 1398 |
| Positive predictive value | 100.0% (98.8%-100.0%) | 304 of 304 |

Template notes make this easy: real notes would not be read perfectly.

## 8. DGF misclassification: sensitivity analysis

**Main data**: the kidney Cox model with DGF from the NLP vs the generator's true flag.

| Covariate | HR with NLP DGF | HR with true DGF | Change |
|---|---|---|---|
| Age at transplant (per 10 years) | 1.04 (0.91-1.19) | 1.04 (0.91-1.19) | +0.0% |
| Deceased donor (vs living) | 0.88 (0.55-1.41) | 0.88 (0.55-1.41) | +0.0% |
| DSA positive at month-1 test | 3.44 (1.77-6.68) | 3.44 (1.77-6.68) | +0.0% |
| Delayed graft function | 1.15 (0.64-2.04) | 1.15 (0.64-2.04) | +0.0% |

**Simulated** (20,000 patients, true DGF HR = 1.8): the same DGF read with errors. Non-differential misclassification pulls the hazard ratio toward 1. With the true flag this sample estimates 1.64 (sampling noise around 1.8); each change below is against that estimate.

| DGF read with | Sensitivity | Specificity | DGF HR (95% CI) | Change vs true DGF |
|---|---|---|---|---|
| no error (true flag) | 100.0% | 100.0% | 1.64 (1.46-1.86) | +0.0% |
| the NLP's measured rates | 100.0% | 100.0% | 1.64 (1.46-1.86) | +0.0% |
| the NLP's worst plausible rates (95% CI lower bounds) | 98.8% | 99.7% | 1.63 (1.44-1.84) | -1.0% |
| a weaker reader | 90.0% | 95.0% | 1.44 (1.28-1.62) | -12.5% |
| a poor reader | 75.0% | 90.0% | 1.36 (1.21-1.53) | -17.3% |

With the NLP as accurate as measured here, DGF misclassification does not move the statistics; a reader at 75% sensitivity would understate the DGF effect noticeably.
