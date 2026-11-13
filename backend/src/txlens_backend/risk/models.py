"""The two candidate models and their small, fixed hyperparameter grids.

Logistic regression is the interpretable baseline: median imputation with missing-value
indicators, standardisation, L2 penalty. Gradient boosting (LightGBM) handles missing values
itself and can model interactions and thresholds. Both are sklearn estimators, so training,
MLflow logging and serving treat them the same way.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from sklearn.base import ClassifierMixin
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

SEED = 20261112


class Kind(StrEnum):
    """Model family."""

    LOGISTIC = "logistic_regression"
    LIGHTGBM = "lightgbm"


GRIDS: Mapping[Kind, tuple[dict[str, Any], ...]] = {
    Kind.LOGISTIC: tuple({"C": c} for c in (0.01, 0.1, 1.0)),
    Kind.LIGHTGBM: tuple(
        {"num_leaves": leaves, "n_estimators": trees} for leaves in (7, 15) for trees in (150, 300)
    ),
}
LIGHTGBM_FIXED: dict[str, Any] = {
    "learning_rate": 0.03, "min_child_samples": 40, "subsample": 0.8, "subsample_freq": 1,
    "colsample_bytree": 0.8, "reg_lambda": 1.0, "random_state": SEED, "n_jobs": 1,
    "deterministic": True, "force_col_wise": True, "verbose": -1,
}  # fmt: skip


def make(kind: Kind, params: Mapping[str, Any]) -> ClassifierMixin:
    """An unfitted model of one family with the given grid point."""
    if kind is Kind.LOGISTIC:
        return Pipeline([
            ("impute", SimpleImputer(strategy="median", add_indicator=True)),
            ("scale", StandardScaler()),
            ("model", LogisticRegression(C=params["C"], max_iter=5000)),
        ])  # fmt: skip
    # Imported here: LightGBM needs the OpenMP runtime, which the API and the worker only
    # load when the champion is a LightGBM model.
    from lightgbm import LGBMClassifier

    return LGBMClassifier(**LIGHTGBM_FIXED, **params)
