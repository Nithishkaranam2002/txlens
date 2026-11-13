"""R vs Python survival results on the same extract (r_parity/, Step 3.1).

The extract is written once as CSV; the R reference (r_parity/R/survival_reference.R, run in
the `txlens-r:local` Docker image built by `make r-build`) and the Python port both read that
file, and every number is compared. Used by r_parity/tests (pytest -m r) and by
`make survival-report`.
"""

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

import numpy as np
import pandas as pd

from txlens_backend.survival.competing import aalen_johansen
from txlens_backend.survival.cox import fit_cox
from txlens_backend.survival.extract import GRAFT_LOSS
from txlens_backend.survival.km import Curve, kaplan_meier, log_rank
from txlens_backend.survival.landmark import (
    YEAR,
    at_landmark,
    landmark_ratio,
    naive_from_time_zero,
    time_varying_ratio,
)

Json = dict[str, Any]  # results as R writes them (parsed JSON), and the Python side's mirror
Pair = tuple[float | None, float | None]  # (R, Python)


class CoxModel(TypedDict):
    covariates: list[str]
    landmark: int


R_IMAGE = os.environ.get("TXLENS_R_IMAGE", "txlens-r:local")
R_SCRIPTS = Path(__file__).resolve().parents[3] / "r_parity" / "R"
COLUMNS = ["time", "status", "group", "age", "organ", "donor_type", "dgf", "dsa_positive",
           "exposure_day"]  # fmt: skip
# Cox models compared: the full model from the month-1 DSA test, and every organ without the
# kidney-only covariates.
COX_MODELS: dict[str, CoxModel] = {
    "full": {"covariates": ["age_per_decade", "deceased_donor", "dsa_positive", "dgf", "heart"],
             "landmark": 30},
    "all_organs": {"covariates": ["age_per_decade", "deceased_donor", "heart"], "landmark": 0},
}  # fmt: skip
# (absolute tolerance, relative tolerance) per quantity. Kaplan-Meier, log-rank and cumulative
# incidence are closed-form: they agree to rounding. Cox estimates come from Newton-Raphson
# with different stopping rules, so they agree to the optimiser's precision.
TOLERANCES = {
    "km_n_risk": (0.0, 0.0), "km_estimate": (1e-10, 0.0), "km_se": (1e-10, 0.0),
    "km_ci": (1e-10, 0.0), "logrank_chisq": (0.0, 1e-8),
    "cif_estimate": (1e-10, 0.0), "cif_se": (1e-10, 0.0), "cif_ci": (1e-10, 0.0),
    "cox_coef": (1e-5, 0.0), "cox_se": (0.0, 1e-4), "immortal_coef": (1e-5, 0.0),
    "immortal_se": (0.0, 1e-4),
}  # fmt: skip


def r_available() -> bool:
    """Docker is installed and the R image has been built."""
    if shutil.which("docker") is None:
        return False
    found = subprocess.run(["docker", "image", "inspect", R_IMAGE], capture_output=True)
    return found.returncode == 0


def write_extract(frame: pd.DataFrame, group: pd.Series, folder: Path) -> None:
    """The shared extract (one row per patient, no identifiers) and the R spec."""
    folder.mkdir(parents=True, exist_ok=True)
    data = frame.assign(group=group.astype(str), exposure_day=frame["dd_cfdna_high_day"])
    data[COLUMNS].to_csv(folder / "extract.csv", index=False)
    spec = {"cox_models": COX_MODELS, "immortal_window": YEAR}
    (folder / "spec.json").write_text(json.dumps(spec))


def read_extract(folder: Path) -> pd.DataFrame:
    """The extract as both sides read it."""
    data = pd.read_csv(folder / "extract.csv", dtype={"group": str})
    return data.rename(columns={"exposure_day": "dd_cfdna_high_day"})


def run_r(folder: Path) -> Json:
    """Run the R reference on the folder's extract; returns its results."""
    subprocess.run(
        ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
         "-v", f"{folder.resolve()}:/work",
         "-v", f"{R_SCRIPTS}:/scripts:ro", R_IMAGE, "Rscript", "/scripts/survival_reference.R",
         "/work/extract.csv", "/work/spec.json", "/work/r_results.json"],
        check=True, capture_output=True,
    )  # fmt: skip
    result: Json = json.loads((folder / "r_results.json").read_text())
    return result


def column(values: np.ndarray) -> list[float | None]:
    """Floats, with NaN and infinity as None (as R writes NA)."""
    return [float(v) if np.isfinite(v) else None for v in values]


def curve_dict(curve: Curve) -> Json:
    """A curve's columns in R's output shape."""
    return {"time": column(curve.time), "n_risk": column(curve.n_risk),
            "n_event": column(curve.n_event), "estimate": column(curve.estimate),
            "se": column(curve.se), "lower": column(curve.lower),
            "upper": column(curve.upper)}  # fmt: skip


def run_python(data: pd.DataFrame) -> Json:
    """The Python port on the same extract, in the R output's shape."""
    loss = (data["status"] == GRAFT_LOSS).astype(int)
    km, cif = {}, {}
    for g in sorted(data["group"].unique()):
        part = data["group"] == g
        km[g] = curve_dict(kaplan_meier(data.loc[part, "time"], loss[part]))
        cif[g] = curve_dict(aalen_johansen(data.loc[part, "time"], data.loc[part, "status"],
                                           GRAFT_LOSS))  # fmt: skip
    cox = {}
    for name, model in COX_MODELS.items():
        fit = fit_cox(at_landmark(data, model["landmark"]), model["covariates"])
        cox[name] = {"n": fit.n, "events": fit.events,
                     "coef": {t.key: t.coef for t in fit.terms},
                     "se": {t.key: t.se for t in fit.terms},
                     "zph_p": {t.key: t.ph_p_value for t in fit.terms}}  # fmt: skip
    designs = {"naive": naive_from_time_zero(data, "dd_cfdna_high_day", YEAR),
               "landmark": landmark_ratio(data, "dd_cfdna_high_day", YEAR),
               "time_varying": time_varying_ratio(data, "dd_cfdna_high_day", YEAR)}  # fmt: skip
    return {
        "km": km, "logrank_chisq": log_rank(data["time"], loss, data["group"]).chi2,
        "cif": cif, "cox": cox,
        "immortal_time": {k: {"coef": v.coef, "se": v.se} for k, v in designs.items()},
    }  # fmt: skip


@dataclass(frozen=True)
class Comparison:
    """One quantity: how many values, the largest differences, and whether within tolerance."""

    quantity: str
    values: int
    max_abs_diff: float
    max_rel_diff: float
    tolerance: str
    passed: bool


def as_list(value: Any) -> list[float | None]:
    """A column as a list (R writes a one-value column as a scalar)."""
    return value if isinstance(value, list) else [value]


def curve_pairs(r: Json, py: Json, keys: list[str]) -> list[Pair]:
    """Matching values of every group's curve columns."""
    if set(r) != set(py):
        raise ValueError(f"groups differ: R {sorted(r)}, Python {sorted(py)}")
    found: list[Pair] = []
    for group in py:
        for key in keys:
            r_values, py_values = as_list(r[group][key]), as_list(py[group][key])
            if len(r_values) != len(py_values):
                raise ValueError(
                    f"{group} {key}: R {len(r_values)} values, Python {len(py_values)}"
                )
            found += zip(r_values, py_values, strict=True)
    return found


def compare(quantity: str, found: list[Pair]) -> Comparison:
    """Largest differences over the pairs (NA must match NA)."""
    abs_tol, rel_tol = TOLERANCES[quantity]
    abs_diff = rel_diff = 0.0
    passed = True
    for a, b in found:
        if a is None or b is None:
            passed &= a is None and b is None
            continue
        diff = abs(a - b)
        rel = diff / abs(a) if a else diff
        abs_diff, rel_diff = max(abs_diff, diff), max(rel_diff, rel)
        passed &= diff <= abs_tol or rel <= rel_tol
    tolerance = f"abs <= {abs_tol:g}" if abs_tol else f"rel <= {rel_tol:g}"
    return Comparison(quantity, len(found), abs_diff, rel_diff, tolerance, passed)


def term_pairs(r: Json, py: Json, key: str) -> Iterator[Pair]:
    """One statistic of every Cox term, model by model (the same terms on both sides)."""
    for m in COX_MODELS:
        if set(r[m][key]) != set(py[m][key]):
            raise ValueError(f"{m}: R terms {sorted(r[m][key])}, Python {sorted(py[m][key])}")
        yield from ((r[m][key][t], py[m][key][t]) for t in py[m][key])


def compare_all(r: Json, py: Json) -> list[Comparison]:
    """Every asserted quantity, R vs Python."""
    r_it, py_it = r["immortal_time"], py["immortal_time"]
    return [
        compare("km_n_risk", curve_pairs(r["km"], py["km"], ["n_risk"])),
        compare("km_estimate", curve_pairs(r["km"], py["km"], ["estimate"])),
        compare("km_se", curve_pairs(r["km"], py["km"], ["se"])),
        compare("km_ci", curve_pairs(r["km"], py["km"], ["lower", "upper"])),
        compare("logrank_chisq", [(r["logrank_chisq"], py["logrank_chisq"])]),
        compare("cif_estimate", curve_pairs(r["cif"], py["cif"], ["estimate"])),
        compare("cif_se", curve_pairs(r["cif"], py["cif"], ["se"])),
        compare("cif_ci", curve_pairs(r["cif"], py["cif"], ["lower", "upper"])),
        compare("cox_coef", list(term_pairs(r["cox"], py["cox"], "coef"))),
        compare("cox_se", list(term_pairs(r["cox"], py["cox"], "se"))),
        compare("immortal_coef", [(r_it[d]["coef"], py_it[d]["coef"]) for d in py_it]),
        compare("immortal_se", [(r_it[d]["se"], py_it[d]["se"]) for d in py_it]),
    ]


@dataclass(frozen=True)
class PhCheck:
    """Schoenfeld test per covariate: R cox.zph vs lifelines (reported, not asserted)."""

    model: str
    covariate: str
    r_p: float
    python_p: float

    @property
    def same_verdict(self) -> bool:
        """Both flag (p < 0.05) or both do not."""
        return (self.r_p < 0.05) == (self.python_p < 0.05)


def ph_checks(r: Json, py: Json) -> list[PhCheck]:
    """Proportional-hazards p-values side by side."""
    return [PhCheck(m, t, float(r["cox"][m]["zph_p"][t]), float(py["cox"][m]["zph_p"][t]))
            for m in COX_MODELS for t in py["cox"][m]["zph_p"]]  # fmt: skip


@dataclass(frozen=True)
class Parity:
    """The comparisons, the PH checks, and R's own output (versions, Gray's test)."""

    comparisons: list[Comparison]
    ph: list[PhCheck]
    r: Json


def parity(frame: pd.DataFrame, group: pd.Series, folder: Path) -> Parity:
    """Write the extract, run both sides on it, compare."""
    write_extract(frame, group, folder)
    r = run_r(folder)
    py = run_python(read_extract(folder))
    return Parity(compare_all(r, py), ph_checks(r, py), r)
