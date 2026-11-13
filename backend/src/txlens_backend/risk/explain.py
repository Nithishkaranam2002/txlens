"""Per-visit feature contributions on the log-odds scale (they add up to the model's logit).

Logistic regression: coefficient x standardised value, with a missing-value indicator's term
added to its feature. LightGBM: exact TreeSHAP values from the library (`pred_contrib`).
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from numpy.typing import NDArray
from sklearn.base import ClassifierMixin
from sklearn.pipeline import Pipeline

from txlens_backend.risk.features import FEATURE_NAMES, FEATURES

LABELS = {f.name: f.label for f in FEATURES}


@dataclass(frozen=True)
class Contribution:
    """How much one feature moved this visit's risk (log-odds; > 0 raises it)."""

    feature: str
    label: str
    value: float | None
    log_odds: float

    def as_dict(self) -> dict[str, object]:
        return {"feature": self.feature, "label": self.label, "value": self.value,
                "log_odds": self.log_odds}  # fmt: skip


def contributions(
    model: ClassifierMixin, x: pd.DataFrame
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """(per-feature contributions, base value) for each row; rows sum to the logit."""
    x = x[list(FEATURE_NAMES)]
    if isinstance(model, LGBMClassifier):
        raw = np.asarray(model.predict(x, pred_contrib=True), dtype=float)
        return raw[:, :-1], raw[:, -1]
    if not isinstance(model, Pipeline):
        raise TypeError(f"no explainer for {type(model).__name__}")
    imputer, scaler, lr = (model.named_steps[s] for s in ("impute", "scale", "model"))
    z = scaler.transform(imputer.transform(x))
    terms = z * lr.coef_[0]
    n = len(FEATURE_NAMES)
    out = terms[:, :n].copy()
    indicator = imputer.indicator_
    if indicator is not None:
        for j, feature_index in enumerate(indicator.features_):
            out[:, feature_index] += terms[:, n + j]
    return out, np.full(len(x), float(lr.intercept_[0]))


def top_contributions(model: ClassifierMixin, row: pd.DataFrame, k: int = 3) -> list[Contribution]:
    """The `k` features that moved one visit's risk the most, largest effect first."""
    values, _ = contributions(model, row)
    order = np.argsort(-np.abs(values[0]))[:k]
    return [
        Contribution(
            feature=FEATURE_NAMES[i], label=LABELS[FEATURE_NAMES[i]],
            value=None if pd.isna(row.iloc[0][FEATURE_NAMES[i]])
            else float(row.iloc[0][FEATURE_NAMES[i]]),
            log_odds=float(values[0][i]),
        )
        for i in order
    ]  # fmt: skip
