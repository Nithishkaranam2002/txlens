# Surveillance engine: day-by-day replay vs ground truth

Generated 2026-11-10 by `make surveillance-report` (evals/src/txlens_evals/surveillance_replay.py). Synthetic data only.

The engine is replayed over each patient's whole follow-up, seeing only data collected up to each day. An **alert** is a medium or high severity flag; low-severity flags (overdue test, tacrolimus outside its range) only count towards the worklist size. The engine supports clinical decisions; it does not diagnose.

> **Read with care:** The synthetic data was generated with the same thresholds and signal shapes that the engine checks (for example, rejection pushes dd-cfDNA above 1% and creatinine up by 10-90%). These results are therefore optimistic. They show that the engine, the replay and the scoring work end to end; they are not evidence of how well these rules would perform on real patients.

**Using surveillance and routine labs only. Kidney: 135 of 231 (58%) rejection episodes flagged before the biopsy, median lead 18 days (IQR 10-32), 31.9 false alarms per 100 patient-years. Heart: 10 of 43 (23%) rejection episodes flagged before the biopsy, median lead 18 days (IQR 10-24), 12.2 false alarms per 100 patient-years.**

## Rejection episodes flagged before the biopsy

Window: from 14 days before the signal onset to the day before the biopsy. Lead time = biopsy date - first day an alert was open in that window.

| Program, labs used | Patients | Episodes flagged | Median lead (IQR) |
|---|---|---|---|
| Kidney, all labs | 1,702 | 226 of 231 (98%) | 8 days (IQR 7-22) |
| Kidney, surveillance and routine labs only | 1,702 | 135 of 231 (58%) | 18 days (IQR 10-32) |
| Heart, all labs | 298 | 38 of 43 (88%) | 7 days (IQR 7-7) |
| Heart, surveillance and routine labs only | 298 | 10 of 43 (23%) | 18 days (IQR 10-24) |

For-cause labs are drawn a week before the biopsy because a clinician was already worried; the rows without them show what scheduled surveillance alone finds.

### By rejection type (surveillance and routine labs only)

| Program | Type | Flagged | Median lead |
|---|---|---|---|
| Kidney | ABMR | 40 of 57 (70%) | 23 days (IQR 12-42) |
| Kidney | TCMR | 44 of 83 (53%) | 11 days (IQR 8-25) |
| Kidney | borderline | 21 of 50 (42%) | 17 days (IQR 12-30) |
| Kidney | mixed | 30 of 41 (73%) | 18 days (IQR 12-42) |
| Heart | ABMR | 1 of 6 (17%) | 26 days |
| Heart | TCMR | 7 of 24 (29%) | 17 days (IQR 3-19) |
| Heart | borderline | 2 of 11 (18%) | 38 days (IQR 5-71) |
| Heart | mixed | 0 of 2 (0%) | - |

## Injury episodes: injury, not necessarily rejection

BK virus nephropathy, CMV infection and acute tubular injury also raise dd-cfDNA and creatinine. Flagging them is expected: the marker shows graft injury, not its cause. The biopsy tells them apart.

| Program | Cause | Flagged before biopsy | Median lead |
|---|---|---|---|
| Kidney | acute tubular injury | 11 of 21 (52%) | 10 days (IQR 6-13) |
| Kidney | bk nephropathy | 25 of 51 (49%) | 18 days (IQR 10-38) |
| Kidney | cmv infection | 8 of 23 (35%) | 8 days (IQR 4-19) |

## Alert onsets and false alarms

Each time a patient goes from no alert to an alert counts once. Onsets within an episode window (to 30 days after its biopsy) belong to that episode; onsets up to 180 days after a proven episode are follow-up of known injury; the rest are false alarms.

| Program, labs used | Patient-years | Rejection | Injury | After a proven episode | False alarms | False alarms per 100 patient-years |
|---|---|---|---|---|---|---|
| Kidney, all labs | 3,873 | 212 | 91 | 24 | 1,237 | 31.9 |
| Kidney, surveillance and routine labs only | 3,873 | 173 | 67 | 53 | 1,237 | 31.9 |
| Heart, all labs | 688 | 42 | 0 | 3 | 84 | 12.2 |
| Heart, surveillance and routine labs only | 688 | 19 | 0 | 7 | 84 | 12.2 |

### Which rules opened alerts (surveillance and routine labs only)

| Program | Rule | Alert onsets |
|---|---|---|
| Kidney | dd_cfdna_rising | 1,119 |
| Kidney | dd_cfdna_threshold | 382 |
| Kidney | creatinine_rise | 167 |
| Kidney | dsa_new_positive | 127 |
| Kidney | egfr_decline | 100 |
| Heart | dd_cfdna_rising | 104 |
| Heart | dd_cfdna_threshold | 18 |

## Alert burden: worklist size per day (2025-01-01 to data cutoff, by center)

"Any flag" includes low-severity housekeeping flags (overdue test, tacrolimus range). Flags here clear only when their rule stops firing; clinicians resolving flags would shrink the list further.

| Program | Center | Any flag: median / p90 | Alerts: median / p90 | Any flag per 100 patients under surveillance (median) |
|---|---|---|---|---|
| Kidney | center_a | 186 / 201 | 45 / 54 | 39 |
| Kidney | center_b | 165 / 178 | 42 / 52 | 39 |
| Kidney | center_c | 117 / 128 | 23 / 29 | 38 |
| Heart | center_a | 51 / 59 | 3 / 4 | 60 |
| Heart | center_b | 41 / 49 | 1 / 4 | 61 |
| Heart | center_c | 29 / 35 | 2 / 3 | 57 |

## Overdue-test rule vs what really happened to each test

An overdue flag for a test that was done means its result never reached the engine: the pipeline quarantined it (bad date or missing value in the lab feed), or the draw was also a for-cause draw, which this lab set leaves out.

| Program | Overdue flags | Truly missed | Result quarantined | Drawn for cause | Other | Missed tests flagged |
|---|---|---|---|---|---|---|
| Kidney | 1,631 | 1,336 of 1,631 (82%) | 271 | 24 | 0 | 1,336 of 1,337 (100%) |
| Heart | 291 | 242 of 291 (83%) | 47 | 2 | 0 | 242 of 242 (100%) |

## Threshold settings: sensitivity vs alert burden

Surveillance and routine labs only. Burden = median number of patients with an alert per day, all centers together.

| Setting | Rejections flagged before biopsy | Median lead | Injury episodes flagged | False alarms per 100 patient-years | Patients with an alert per day (median) |
|---|---|---|---|---|---|
| kidney: 0.5% watch / 1.0% high + trend (shipped) | 135 of 231 (58%) | 18 days (IQR 10-32) | 44 of 95 (46%) | 31.9 | 110 |
| kidney: 1.0% high only + trend | 132 of 231 (57%) | 17 days (IQR 9-32) | 37 of 95 (39%) | 30.2 | 104 |
| kidney: 0.5% / 1.0%, no trend rule | 120 of 231 (52%) | 15 days (IQR 8-28) | 33 of 95 (35%) | 11.1 | 50 |
| kidney: 0.7% watch / 1.0% high + trend | 133 of 231 (58%) | 17 days (IQR 10-32) | 39 of 95 (41%) | 30.3 | 106 |
| heart: 0.2% high + trend (shipped) | 10 of 43 (23%) | 18 days (IQR 10-24) | 0 of 0 | 12.2 | 6 |
| heart: 0.15% high + trend | 12 of 43 (28%) | 19 days (IQR 13-26) | 0 of 0 | 12.9 | 6 |
| heart: 0.25% high + trend | 9 of 43 (21%) | 19 days (IQR 10-25) | 0 of 0 | 12.1 | 6 |

## Oncology config: same engine, different YAML

A small synthetic cohort after curative cancer treatment, with a generic tumor DNA marker tested every 3 months (config `oncology_tumor_dna`). No code was changed to run it.

- Patients: 200 (459 patient-years)
- Recurrences flagged before clinical recurrence: 39 of 61 (64%), median lead 40 days (IQR 18-63)
- False alarms: 34 (7.4 per 100 patient-years)
- Overdue flags: 119, of which 119 of 119 (100%) for a truly missed test; 119 of 119 (100%) missed tests flagged

With testing every 3 months and a signal that starts 30-120 days before recurrence, some recurrences have no test inside the signal window at all; those cannot be caught by any threshold.

## Known gaps

- **Optimistic by construction.** The synthetic data was generated with the same thresholds and signal shapes that the engine checks (for example, rejection pushes dd-cfDNA above 1% and creatinine up by 10-90%). These results are therefore optimistic. They show that the engine, the replay and the scoring work end to end; they are not evidence of how well these rules would perform on real patients.
- Flags are cleared only when their rule stops firing. Clinicians acknowledging and resolving flags is not simulated, so the worklist sizes are an upper bound.
- The replay reads the ground-truth CSVs. A database test checks that the database path gives identical flags for the same data (backend/tests/test_db_surveillance.py).
- Tacrolimus levels in the generator are noisy by design, so the low-severity range flag fires often. It adds to the worklist but not to the alert counts.
- Heart transplant rejection is also screened by protocol biopsies, which are not scored here.
