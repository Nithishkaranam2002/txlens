"""The Python port matches the R reference on the same extract (pytest -m r).

Kaplan-Meier (estimate, Greenwood SE, log CI, numbers at risk), the log-rank statistic,
Aalen-Johansen cumulative incidence (estimate, infinitesimal-jackknife SE, CI), Cox
coefficients and SEs (two models), and the naive / landmark / time-varying hazard ratios of
the immortal-time demonstration, each within the tolerance in TOLERANCES. Schoenfeld p-values
differ by method (see r_parity/README.md) and are only reported.
"""

from pathlib import Path

import pandas as pd
import pytest

from txlens_backend.survival.simulate import simulate
from txlens_evals.survival_parity import TOLERANCES, Comparison, parity

pytestmark = [pytest.mark.r, pytest.mark.db]


def check(comparisons: list[Comparison]) -> None:
    assert {c.quantity for c in comparisons} == set(TOLERANCES)
    failed = [c for c in comparisons if not c.passed]
    assert not failed, failed
    assert all(c.values > 0 for c in comparisons)


def test_simulated_extract_matches_r(work: Path) -> None:
    frame = simulate()
    result = parity(frame, frame["donor_type"], work)
    check(result.comparisons)
    assert str(result.r["survival_version"]).startswith("3.")
    assert len(result.ph) == 7  # four kidney-model and three all-organ covariates


def test_omop_extract_matches_r(omop_extract: pd.DataFrame, work: Path) -> None:
    check(parity(omop_extract, omop_extract["organ"], work).comparisons)
