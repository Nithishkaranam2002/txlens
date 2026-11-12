"""The auto-generated methods paragraph: a fixed template filled from the analysis, not an LLM."""

import platform
from dataclasses import dataclass
from importlib.metadata import version

from txlens_backend.survival.suppression import Count


@dataclass(frozen=True)
class MethodsFacts:
    """What the paragraph states (counts already suppressed)."""

    population: str
    n: Count
    events: Count
    grouping_label: str | None  # None: a single curve
    landmark_days: int
    cox_covariates: list[str]
    cox_n: Count | None  # None: no Cox model was fitted
    cox_events: Count | None
    cox_landmark_days: int
    ph_violations: list[str]
    threshold: int


def software() -> str:
    """Python and lifelines versions."""
    return f"Python {platform.python_version()} with lifelines {version('lifelines')}"


def in_sentence(label: str) -> str:
    """A label inside a sentence: first letter lowercased unless it starts with an acronym
    ("Donor type" -> "donor type", "DSA at month-1 test" unchanged)."""
    first = label.split(" ", 1)[0]
    return label if first.isupper() or not label else label[0].lower() + label[1:]


def landmark_clause(days: int, what: str) -> str:
    """How follow-up starts, for groups or covariates known only after transplant."""
    if days == 0:
        return ""
    return (
        f" Because {what} is known only during follow-up, follow-up started at a landmark "
        f"of day {days} after transplant, among patients alive with a functioning graft "
        "that day, and only information available by then was used (avoiding immortal "
        "time bias)."
    )


def cox_sentences(f: MethodsFacts) -> str:
    """The Cox model and its proportional-hazards check."""
    if f.cox_n is None:
        return ""
    landmark = (
        f" from a landmark at day {f.cox_landmark_days}"
        if f.cox_landmark_days > f.landmark_days
        else ""
    )
    ph = (
        "no covariate showed evidence against proportional hazards (p >= 0.05)"
        if not f.ph_violations
        else f"evidence against proportional hazards (p < 0.05) was found for "
        f"{', '.join(f.ph_violations)}, so those hazard ratios are averages over follow-up"
    )
    covariates = "; ".join(in_sentence(c) for c in f.cox_covariates)
    return (
        f" Hazard ratios came from a Cox proportional hazards model (Efron's method for "
        f"tied times) with {covariates}, fitted on complete cases "
        f"({f.cox_n} patients, {f.cox_events} graft losses){landmark}. Proportional hazards "
        "were checked with scaled Schoenfeld residuals against the Kaplan-Meier transform "
        f"of time: {ph}."
    )


def methods_paragraph(f: MethodsFacts) -> str:
    """One paragraph a reader can paste into a methods section."""
    grouping = (
        f" Groups were compared by {in_sentence(f.grouping_label)} with the log-rank test."
        if f.grouping_label
        else ""
    )
    what = in_sentence(f.grouping_label) if f.grouping_label else "the population"
    return (
        f"Synthetic data, for demonstration only. We analysed {f.n} transplant recipients "
        f"({f.population}). Time zero was the transplant date. The outcome was death-censored "
        "graft loss: follow-up ended at graft loss, at death with a functioning graft "
        f"(censored), or at the end of the observation period ({f.events} graft losses)."
        f"{landmark_clause(f.landmark_days, what)} Graft survival was estimated with the "
        "Kaplan-Meier method, with 95% confidence intervals from Greenwood's variance on the "
        f"log scale.{grouping} Because death with a functioning graft prevents later graft "
        "loss, the cumulative incidence of graft loss was estimated with the Aalen-Johansen "
        "estimator with death as a competing event (infinitesimal-jackknife standard errors); "
        f"1 - Kaplan-Meier, which overstates that risk, is shown for comparison."
        f'{cox_sentences(f)} Counts of 1 to {f.threshold - 1} are shown as "<{f.threshold}" '
        f"and curve steps are pooled so that each shows at least {f.threshold} events. "
        f"Analyses used {software()}; the implementation is checked against the R survival "
        "package (r_parity/)."
    )
