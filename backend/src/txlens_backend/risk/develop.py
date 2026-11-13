"""Model development: patient-level splits, cross-validation, the champion rule, test predictions.

1. Centers A+B are split by patient: 80% development, 20% internal test (never the same patient
   in both). Five-fold patient-grouped cross-validation on development picks each family's
   hyperparameters and the champion.
2. Champion rule (fixed before any test result was seen): gradient boosting wins only if its
   mean cross-validated AUPRC beats logistic regression by at least 0.01; otherwise the
   interpretable model wins.
3. Internal test: models refit on development, scored once on the 20%.
4. External test: models refit on all of A+B, scored once on center C.
5. Production: the champion refit on every center (its metrics are the ones from 3 and 4).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.base import ClassifierMixin
from sklearn.model_selection import StratifiedGroupKFold

from txlens_backend.risk.features import FEATURE_NAMES
from txlens_backend.risk.metrics import auprc, auroc
from txlens_backend.risk.models import GRIDS, SEED, Kind, make

TRAIN_CENTERS = ("center_a", "center_b")
EXTERNAL_CENTER = "center_c"
FOLDS = 5
CHAMPION_MARGIN = 0.01


@dataclass(frozen=True)
class CvResult:
    """Mean cross-validated scores of one grid point."""

    kind: Kind
    params: Mapping[str, Any]
    auprc: float
    auroc: float


@dataclass
class Development:
    """Everything the evaluation and the registry need from one development run."""

    label: str
    cv: list[CvResult]
    best: dict[Kind, Mapping[str, Any]]
    champion: Kind
    development: pd.DataFrame  # with out-of-fold scores p_<kind> from the chosen grid points
    internal: pd.DataFrame  # internal test rows with p_<kind>
    external: pd.DataFrame  # center C rows with p_<kind>
    models: dict[str, ClassifierMixin] = field(default_factory=dict)


def score_column(kind: Kind) -> str:
    return f"p_{kind.value}"


def labelled(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    """Visits with a fully observed outcome window."""
    return frame[frame[label].notna()].reset_index(drop=True)


def patient_folds(frame: pd.DataFrame, label: str, n: int = FOLDS) -> list[tuple[Any, Any]]:
    """Patient-grouped folds, stratified so each fold gets its share of rejections."""
    splitter = StratifiedGroupKFold(n_splits=n, shuffle=True, random_state=SEED)
    return list(splitter.split(frame, frame[label].astype(int), groups=frame["key"]))


def split_internal(frame: pd.DataFrame, label: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(development, internal test): one of five patient-grouped folds is held out."""
    train, test = patient_folds(frame, label)[0]
    return frame.iloc[train].reset_index(drop=True), frame.iloc[test].reset_index(drop=True)


def fit(kind: Kind, params: Mapping[str, Any], frame: pd.DataFrame, label: str) -> ClassifierMixin:
    model = make(kind, params)
    model.fit(frame[list(FEATURE_NAMES)], frame[label].astype(int))
    return model


def predict(model: ClassifierMixin, frame: pd.DataFrame) -> np.ndarray:
    return np.asarray(model.predict_proba(frame[list(FEATURE_NAMES)])[:, 1], dtype=float)


def cross_validate(
    frame: pd.DataFrame, label: str
) -> tuple[list[CvResult], dict[Kind, np.ndarray]]:
    """Every grid point's mean fold scores, and the out-of-fold scores of each family's best."""
    results: list[CvResult] = []
    best_scores: dict[Kind, np.ndarray] = {}
    y = frame[label].to_numpy(dtype=float)
    for kind, grid in GRIDS.items():
        best: tuple[float, np.ndarray] | None = None
        for params in grid:
            folds = patient_folds(frame, label)
            scores = np.zeros(len(frame))
            fold_auprc, fold_auroc = [], []
            for train, test in folds:
                model = fit(kind, params, frame.iloc[train], label)
                scores[test] = predict(model, frame.iloc[test])
                fold_auprc.append(auprc(y[test], scores[test]))
                fold_auroc.append(auroc(y[test], scores[test]))
            result = CvResult(kind, params, float(np.nanmean(fold_auprc)),
                              float(np.nanmean(fold_auroc)))  # fmt: skip
            results.append(result)
            if best is None or result.auprc > best[0]:
                best = (result.auprc, scores)
        assert best is not None
        best_scores[kind] = best[1]
    return results, best_scores


def best_params(cv: list[CvResult]) -> dict[Kind, Mapping[str, Any]]:
    """Each family's grid point with the highest mean AUPRC (first one on ties)."""
    best: dict[Kind, CvResult] = {}
    for r in cv:
        if r.kind not in best or r.auprc > best[r.kind].auprc:
            best[r.kind] = r
    return {k: r.params for k, r in best.items()}


def champion(cv: list[CvResult]) -> Kind:
    """Gradient boosting only if it beats logistic regression's AUPRC by the margin."""
    top = {k: max(r.auprc for r in cv if r.kind is k) for k in Kind}
    return (
        Kind.LIGHTGBM
        if top[Kind.LIGHTGBM] >= top[Kind.LOGISTIC] + CHAMPION_MARGIN
        else Kind.LOGISTIC
    )


def develop(frame: pd.DataFrame, label: str = "label") -> Development:
    """Run the whole design on a visit frame (all centers)."""
    data = labelled(frame, label)
    ab = data[data["tenant"].isin(TRAIN_CENTERS)].reset_index(drop=True)
    external = data[data["tenant"] == EXTERNAL_CENTER].reset_index(drop=True)
    dev, internal = split_internal(ab, label)
    cv, oof = cross_validate(dev, label)
    best = best_params(cv)
    dev, internal, external = dev.copy(), internal.copy(), external.copy()
    models: dict[str, ClassifierMixin] = {}
    for kind in Kind:
        dev[score_column(kind)] = oof[kind]
        models[f"internal_{kind.value}"] = fit(kind, best[kind], dev, label)
        internal[score_column(kind)] = predict(models[f"internal_{kind.value}"], internal)
        models[f"external_{kind.value}"] = fit(kind, best[kind], ab, label)
        external[score_column(kind)] = predict(models[f"external_{kind.value}"], external)
    return Development(label, cv, best, champion(cv), dev, internal, external, models)


def production_model(development: Development, frame: pd.DataFrame) -> ClassifierMixin:
    """The champion refit on every labelled visit of every center."""
    data = labelled(frame, development.label)
    kind = development.champion
    return fit(kind, development.best[kind], data, development.label)
