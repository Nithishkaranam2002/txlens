# TxLens survival analysis: the R reference ("research") version.
#
# Usage: Rscript survival_reference.R <extract.csv> <spec.json> <results.json>
#
# The extract has one row per transplant recipient: time (days), status (0 censored,
# 1 graft loss, 2 death with a functioning graft), group, age, organ, donor_type, dgf,
# dsa_positive and exposure_day (first dd-cfDNA >= 1%; NA if never). Synthetic data only.
#
# Everything uses the survival package's defaults (Efron ties, "log" confidence
# intervals, infinitesimal-jackknife standard errors for multi-state curves); the
# Python port in backend/src/txlens_backend/survival/ is checked against this output.

suppressPackageStartupMessages({
  library(survival)
  library(cmprsk)
  library(jsonlite)
})

args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 3)
data <- read.csv(args[1], na.strings = "")
spec <- fromJSON(args[2], simplifyVector = TRUE)
data$group <- as.character(data$group)

# Covariates, defined exactly as the Python port does (cox.py covariate_frame).
data$age_per_decade <- data$age / 10
data$deceased_donor <- as.numeric(data$donor_type == "deceased")
data$heart <- as.numeric(data$organ == "heart")
data$event <- as.numeric(data$status == 1)

finite_or_na <- function(x) ifelse(is.finite(x), x, NA)

# Kaplan-Meier (death-censored graft survival) per group, survfit defaults.
km_by_group <- function(d) {
  out <- list()
  for (g in sort(unique(d$group))) {
    fit <- survfit(Surv(time, event) ~ 1, data = d[d$group == g, ])
    out[[g]] <- list(
      time = fit$time, n_risk = fit$n.risk, n_event = fit$n.event,
      estimate = fit$surv, se = finite_or_na(fit$std.err),
      lower = finite_or_na(fit$lower), upper = finite_or_na(fit$upper)
    )
  }
  out
}

# Aalen-Johansen cumulative incidence of graft loss (death competing), per group.
cif_by_group <- function(d) {
  out <- list()
  for (g in sort(unique(d$group))) {
    part <- d[d$group == g, ]
    part$state <- factor(part$status, levels = 0:2, labels = c("censor", "loss", "death"))
    fit <- survfit(Surv(time, state) ~ 1, data = part, id = seq_len(nrow(part)))
    k <- which(fit$states == "loss")
    out[[g]] <- list(
      time = fit$time, estimate = fit$pstate[, k], se = finite_or_na(fit$std.err[, k]),
      lower = finite_or_na(fit$lower[, k]), upper = finite_or_na(fit$upper[, k])
    )
  }
  out
}

at_landmark <- function(d, day) {
  if (day <= 0) return(d)
  d <- d[d$time > day, ]
  d$time <- d$time - day
  d
}

# Cox on complete cases; covariates constant in them are dropped (as in the port).
cox_model <- function(d, covariates, landmark) {
  d <- at_landmark(d, landmark)
  d <- d[complete.cases(d[, covariates]), ]
  covariates <- covariates[sapply(covariates, function(c) length(unique(d[[c]])) > 1)]
  f <- as.formula(paste("Surv(time, event) ~", paste(covariates, collapse = " + ")))
  fit <- coxph(f, data = d, ties = "efron")
  zph <- cox.zph(fit, transform = "km")
  list(
    n = fit$n, events = fit$nevent,
    coef = as.list(coef(fit)), se = as.list(sqrt(diag(vcov(fit)))),
    zph_p = as.list(zph$table[covariates, "p"]),
    zph_global_p = zph$table["GLOBAL", "p"]
  )
}

one_term <- function(fit) list(coef = unname(coef(fit)), se = unname(sqrt(diag(vcov(fit)))))

# The immortal-time demonstration: naive, landmark and time-varying designs.
immortal_time <- function(d, window) {
  exposed_by <- function(x, day) as.numeric(!is.na(x$exposure_day) & x$exposure_day <= day)
  naive <- d
  naive$exposed <- exposed_by(d, window)
  lm <- at_landmark(d, window)
  lm$exposed <- exposed_by(lm, window)
  # Exposed from the first high result if it came within the window and before the end of
  # follow-up (a result on the event day counts as after it).
  base <- data.frame(id = seq_len(nrow(d)), futime = d$time, lost = d$event)
  base$switch <- ifelse(!is.na(d$exposure_day) & d$exposure_day <= window &
                          d$exposure_day < d$time, d$exposure_day, NA)
  tv <- tmerge(base[, c("id", "futime", "lost")], base, id = id,
               outcome = event(futime, lost))
  switches <- base[!is.na(base$switch), c("id", "switch")]
  tv <- tmerge(tv, switches, id = id, exposed = tdc(switch))
  tv$exposed[is.na(tv$exposed)] <- 0
  list(
    naive = one_term(coxph(Surv(time, event) ~ exposed, data = naive, ties = "efron")),
    landmark = one_term(coxph(Surv(time, event) ~ exposed, data = lm, ties = "efron")),
    time_varying = one_term(coxph(Surv(tstart, tstop, outcome) ~ exposed, data = tv,
                                  ties = "efron"))
  )
}

gray <- cuminc(data$time, data$status, data$group, cencode = 0)$Tests
results <- list(
  r_version = R.version.string,
  survival_version = as.character(packageVersion("survival")),
  cmprsk_version = as.character(packageVersion("cmprsk")),
  km = km_by_group(data),
  logrank_chisq = survdiff(Surv(time, event) ~ group, data = data)$chisq,
  cif = cif_by_group(data),
  gray_test = list(stat = unname(gray[, "stat"]), p = unname(gray[, "pv"])),
  cox = lapply(spec$cox_models, function(m) cox_model(data, m$covariates, m$landmark)),
  immortal_time = immortal_time(data, spec$immortal_window)
)
names(results$cox) <- names(spec$cox_models)
write(toJSON(results, auto_unbox = TRUE, digits = NA, na = "null"), args[3])
