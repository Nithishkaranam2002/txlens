# R parity: survival analysis

`R/survival_reference.R` is the reference ("research") version of the TxLens survival analysis,
written with R's `survival` and `cmprsk` packages. The Python port in
`backend/src/txlens_backend/survival/` is what the API runs. `tests/test_parity.py` runs both
on the same extract and checks that they agree. Synthetic data only.

```
make r-build    # Docker image txlens-r:local: R 4.4.2 (survival 3.7.0), cmprsk 2.2.12, jsonlite
make r-parity   # pytest -m r (needs Postgres for the OMOP extract)
```

There is no R installation on the host: R runs only in the image (`infra/r.Dockerfile`). The image
is pinned by digest, and CRAN is a dated Posit Package Manager snapshot (2025-02-27), so package
versions never change underneath the test. CI runs the same image in its own job (`r-parity`).

## What is compared

`evals/src/txlens_evals/survival_parity.py` writes one CSV extract. R and Python both read it,
and every value is compared. Two extracts are used:

1. **Simulated** (`survival/simulate.py`): 3,000 patients with known hazard ratios, tied
   (whole-day) times and competing deaths.
2. **OMOP**: 1,000 generated patients loaded into a throwaway database, then read back by the
   same extract SQL the API uses.

| Quantity | Tolerance | Measured (see `evals/reports/survival.md`) |
|---|---|---|
| Kaplan-Meier S(t), number at risk | 1e-10 absolute (at risk: exact) | ~1e-15 |
| KM standard error and 95% CI | 1e-10 | ~1e-15 |
| Log-rank chi-square | 1e-8 relative | ~1e-15 |
| Cumulative incidence (Aalen-Johansen), SE, CI | 1e-10 | ~1e-16 |
| Cox coefficients | 1e-5 absolute | ~1e-7 to 1e-9 |
| Cox standard errors | 1e-4 relative | ~1e-8 |
| Naive / landmark / time-varying HR (immortal time) | as Cox | ~1e-6 |

The closed-form estimators (KM, log-rank, Aalen-Johansen) agree to floating-point rounding.
Cox estimates come from iterative fitting with different stopping rules, so they agree to the
optimiser's precision.

**Reported, not asserted:** Schoenfeld proportional-hazards p-values, and Gray's test (R only).
See below.

## Where the libraries differ, and how the port matches R

| Topic | R (`survival`, `cmprsk`) | lifelines / statsmodels default | What the port does |
|---|---|---|---|
| **Ties in Cox** | `coxph(ties = "efron")` is the default | lifelines: Efron (the only method). statsmodels `PHReg`: **Breslow** by default | lifelines, so Efron in both. statsmodels is not used for Cox: with tied whole-day times, Breslow gives different coefficients |
| **Cox convergence** | Newton-Raphson; stops at a 1e-9 relative change in log-likelihood; halves steps that overshoot | lifelines: `precision` 1e-7 and initial `step_size` 0.95 | `FIT_OPTIONS = {"precision": 1e-10, "step_size": 0.5}`. With the defaults, coefficients were ~1.5e-5 from R, and the time-varying fit diverged on the OMOP extract (R converged) |
| **KM standard error** | `std.err` is the Greenwood SE of log S(t) (of the cumulative hazard), not of S(t) | lifelines exposes no SE column | Greenwood on the log scale, computed from lifelines' event table |
| **KM confidence interval** | `conf.type = "log"`: S * exp(+-1.96 se), capped at 1; NA when S = 0 | lifelines: "exponential Greenwood" (**log-log**) | R's "log" interval, computed ourselves. log-log is often better near 0 and 1, but parity with R's default comes first |
| **Cumulative incidence** | `survfit(Surv(t, factor(status)))`: Aalen-Johansen | lifelines `AalenJohansenFitter` **jitters tied event times** by default, changing the estimate | Aalen-Johansen written in numpy (`competing.py`), with no jitter |
| **CIF standard error** | Multi-state `survfit`: infinitesimal jackknife (the sum of each patient's squared influence) | lifelines: a delta-method (Greenwood-type) variance. `cmprsk::cuminc`: Gray's variance | Infinitesimal jackknife (`competing.py`); it matches R to ~1e-16. `cuminc` is used only for Gray's test |
| **CIF interval** | "log" on the probability: p * exp(+-z se / p), capped at 1; NA at p = 0 | | The same |
| **Log-rank** | `survdiff` (rho = 0), hypergeometric variance | `multivariate_logrank_test`: the same statistic | lifelines |
| **Proportional-hazards test** | `cox.zph` (survival >= 3.0): a score test using the full information matrix, with `transform = "km"`; per term and **GLOBAL** | lifelines `proportional_hazard_test`: the older Grambsch-Therneau approximation (regression of scaled Schoenfeld residuals on transformed time); per covariate only, no global test | lifelines with `time_transform = "km"`. p-values differ (e.g. 0.27 vs 0.37), so the report compares *verdicts* at p < 0.05 and does not assert values |
| **Gray's test** (CIF across groups) | `cmprsk::cuminc` | Not available in Python | Reported from R only; the API shows the log-rank test and the curves |
| **Time-varying covariate** | `tmerge(..., tdc(day))`: exposed from the day of the first high result; an exposure on or after the last follow-up day is ignored | `CoxTimeVaryingFitter` needs start/stop rows | `landmark.start_stop()` builds the same rows: an exposure on the event day counts as *after* it, in both |
| **Landmark** | Our script: keep `time > L`, then `time - L` | (none) | `at_landmark()` does the same (strictly after the landmark) |
| **Constant / missing covariates** | `coxph` returns NA for an aliased (constant) term; rows with NA are dropped | lifelines errors on NaN or constant columns | Both sides use complete cases and drop constant covariates explicitly, with a warning (e.g. "heart" once only kidney grafts remain) |
| **Censoring at an event time** | Censored at t means still at risk at t | Same | Same |
| **Cox SEs** | Model-based (robust only with `cluster()`) | Model-based | Model-based |

## Notes

- The R script is plain base R plus `survival` and `cmprsk`. It is what a researcher would
  write, and does not try to look like the Python.
- A difference in an asserted quantity fails the `r-parity` CI job. Change a default on one
  side only together with this table and the tolerance in `survival_parity.TOLERANCES`.
