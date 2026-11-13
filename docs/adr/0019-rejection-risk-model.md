# ADR 0019: Rejection-risk model: leakage-safe design, validation, and serving

**Status:** accepted (2026-11-12)

## Context
Step 3.2 adds a model that, at each scheduled dd-cfDNA surveillance visit, estimates the
chance of a biopsy-proven rejection (TCMR, ABMR or mixed) in the next 90 days. It must:
- use only data available at the visit;
- be validated by patient and on a center it never saw;
- be compared with the existing rule engine at the same alert burden;
- run like a real model: tracked, versioned, served with the version on every prediction,
  scored nightly, and monitored for drift.

It is trained on synthetic data, is not clinically validated, and is decision support
only. That label appears in the API response, the UI, the model card and the report.
Researchers must never see an individual score.

What the data allows:
- **The draw reason (scheduled or for-cause) never reaches OMOP.** The Step 1.6 for-cause
  exclusion was an evaluation-only rule that read the generator's ground truth.
- **Rejection results reach OMOP two ways.** Approved pathology reports are dated on the
  biopsy. Clinic-note NLP is dated when the note was written, usually days later. Many
  reports are still in the review queue.
- **The generator's rejection signal is narrow.** dd-cfDNA and creatinine ramp up 14-56
  days before the biopsy, and DSA appears before ABMR. Tacrolimus, donor type, DGF and age
  have no built-in effect.

## Decision
- **Prediction point and window.**
  - The model predicts once a visit's results are back, so features use facts dated on or
    before the visit day.
  - The label window is (visit, visit + 90 days].
  - A visit whose window is not fully observed has no label: follow-up ends, or death or
    graft loss happens first without a rejection biopsy.
- **A visit is defined by the schedule only.**
  - It is the first dd-cfDNA draw inside a due date's ±14-day window, one per due date.
  - Both facts are known on the draw day, so the nightly job applies the same rule.
  - "First", not "nearest": the nearest draw may be one that has not happened yet.
  - A draw is never dropped because a biopsy follows, because that is future information
    and would remove exactly the visits before a rejection.
  - The evaluation measures how many kept visits were truly for-cause: 1.4% of all kept
    visits, but 16.3% of rejection visits.
  - It reports two sensitivity analyses: (a) also dropping draws within 14 days before a
    biopsy; (b) visits taken from the true draw reason.
- **No feature can look ahead, and a test proves it.**
  - Features are pure functions of a dated patient record up to the visit.
  - dd-cfDNA history uses scheduled visits only.
  - "Prior rejection" uses biopsy results recorded strictly before the visit, by the date
    the result was recorded, not the biopsy date.
  - DGF counts from the day it was recorded.
  - The test changes, deletes and adds everything dated after the visit, and requires every
    feature to be unchanged. It runs on hand-built records and on records loaded from the
    API database.
- **Labels from OMOP, checked against the truth.**
  - A note's rejection result counts as biopsy-proven when a biopsy precedes it by at most
    14 days, and is tied to that biopsy.
  - On the loaded data this recovers 213 of 213 true rejection biopsies with no false ones.
  - Borderline is negative in the main analysis and positive in a sensitivity analysis
    (only 4 of 61 borderline biopsies reach OMOP).
- **Validation design, fixed before testing.**
  - Centers A+B are split by patient: 80% development, 20% internal test. Five-fold
    patient-grouped stratified cross-validation on development picks hyperparameters.
  - Center C is the external test, never used for training or model choice.
  - Champion rule: gradient boosting wins only if its cross-validated AUPRC beats logistic
    regression by at least 0.01. Logistic regression won.
  - The registered production version is the champion refit on all centers. Its metrics are
    the internal and external test results it did not take part in.
- **Metrics.**
  - AUROC and AUPRC (with prevalence), Brier score, and calibration intercept and slope,
    with 95% patient-level bootstrap intervals.
  - Decision curves.
  - The rule engine at the same alert burden: the model alerts on the same share of visits
    as "any medium or high flag". Reported for visits and episodes, paired by patient, plus
    a threshold fixed in advance.
  - Fairness by age band and sex, with groups of fewer than 11 rejection visits not scored.
  - PSI drift between centers and over time.
- **Explanations on the log-odds scale.**
  - Logistic regression: coefficient × standardised value, plus the missing-indicator term.
  - LightGBM: exact TreeSHAP from the library (`pred_contrib`), with no `shap` dependency.
  - Contributions plus the base value add up to the logit; a test checks this.
  - The UI says they are relative to an average visit.
- **MLflow tracking and registry.**
  - SQLite store in `mlruns/` (git-ignored), with an optional UI service in compose.
  - Each run logs parameters, CV and test metrics, the git SHA, the extract date, the
    evaluation JSON, the model card and the drift reference.
  - The production model is registered as `txlens-rejection-risk`, a new version each
    training run, with alias `champion`. Version tags hold the alert threshold.
  - The threshold is set so the model alerts on as many training visits as the rule engine
    did.
  - Models are cloudpickled, so only this deployment's own registry may be loaded.
- **Serving.**
  - `app.risk_prediction` stores each score with the model name and version, inputs, top
    contributions, source (nightly or on demand) and scorer. Rows are insert-only, and one
    row is kept per visit and version. Tenant RLS is forced.
  - `GET /patients/{id}/risk` is for clinicians (identified) and data stewards
    (pseudonymised, shifted dates). Researchers and admins get 403, the frontend never
    requests it for them, and the copilot's SQL guard rejects the table.
  - `POST /patients/{id}/risk/score` is for clinicians only. Every view and score is audited
    with the version.
- **Nightly scoring in the surveillance workflow.**
  - After the rule engine, each center is scored in its own activity.
  - A missing model or a scoring failure is logged and never blocks flags.
  - Each run stores score and per-feature PSI against a reference stored with the version.
    The reference is the same population the nightly job scores: each active patient's
    latest visit at training time.
  - Comparing against all training visits instead read as heavy drift (PSI 1.5) simply
    because latest visits are later in follow-up.
- **Cross-center pooling is a governance decision.** Training reads each center through its
  own RLS-scoped transaction and pools the visits with no identifiers beyond an internal
  key. With real data this needs data-sharing agreements between centers. Synthetic data
  makes it safe here.

## Alternatives
- **Drop draws within 14 days before a biopsy (the obvious "for-cause" proxy).** It uses
  the future, could never run live, and removes the most informative visits. Kept as
  sensitivity analysis (a): external AUROC falls from 0.80 to 0.74.
- **Ground-truth labels for training.** Cleaner, but the training path would not work on
  real data, where only OMOP exists. Kept as a check; here they agree 100%.
- **The `shap` package or scikit-learn's HistGradientBoosting.** `shap` is a heavier
  dependency (numba). LightGBM's built-in TreeSHAP is exact and free. LightGBM needs OpenMP:
  CI and Linux images have it, and on macOS the Makefile falls back to scikit-learn's bundled
  copy. LightGBM is imported only when a LightGBM model is used.
- **A model server (MLflow serving, a separate container).** That is another service to
  secure and multi-tenant. The API loads the champion in-process, cached per version, and
  scoring a patient takes milliseconds.
- **Showing researchers aggregate risk distributions.** Out of scope. Aggregate model
  output would need the same small-cell and differencing guards as cohorts (ADR 0018).

## Consequences
- **The honest headline is "as good as the rules, not better".** At the rule engine's alert
  rate the model catches about the same share of rejection visits; the paired difference is
  -1.3 points (-7.8 to +5.5) on center C. Its value is a ranked, calibrated, explained
  score and higher net benefit than the rules from a 5% risk threshold upward. It does not
  catch extra rejections for the same burden.
- **Retraining is one command** (`make risk-train`). The report (`make risk-report`) checks
  that the registered champion is the model it evaluated, and says so when it is not.
- **The nightly job (02:00 UTC) now also writes predictions.** A stale worker without the
  new activity makes scoring fail for the centers it picks up. Surveillance still completes,
  and the run result shows an empty model version for those centers: restart workers after
  deploying.
- **Real-world performance is unknown.** The model has only seen a generator whose signal is
  built in. Any clinical use would need prospective validation, a regulatory route and
  per-center calibration.
