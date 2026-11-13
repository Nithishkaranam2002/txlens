"""MLflow tracking and model registry for the rejection-risk model.

A training run logs its parameters, cross-validation and test metrics, the model card and the
drift reference, then registers the production model as a new version of
`txlens-rejection-risk` and points the `champion` alias at it. Serving loads `@champion` and
records the version number on every prediction. Models are stored with cloudpickle, so only a
registry this deployment writes to may be loaded (never one from an untrusted source).
"""

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import mlflow
import mlflow.sklearn
import pandas as pd
from mlflow import MlflowClient
from mlflow.models import infer_signature
from sklearn.base import ClassifierMixin

from txlens_backend.risk import LABEL_TEXT
from txlens_backend.risk.develop import Development, labelled
from txlens_backend.risk.evaluate import Summary
from txlens_backend.risk.features import FEATURE_NAMES
from txlens_backend.risk.metrics import Bins, reference_bins, threshold_for_rate

MODEL_NAME = "txlens-rejection-risk"
ALIAS = "champion"
EXPERIMENT = "rejection-risk"
INTENDED_USE = ("Decision support at scheduled transplant surveillance visits: ranks visits by "
                "estimated 90-day risk of biopsy-proven rejection. Not a diagnosis.")  # fmt: skip
REFERENCE_FILE = "drift_reference.json"
CARD_FILE = "model_card.md"


@dataclass(frozen=True)
class Reference:
    """Training distributions the nightly job compares new visits against."""

    features: dict[str, Bins]
    score: Bins

    def as_dict(self) -> dict[str, Any]:
        return {"features": {k: v.as_dict() for k, v in self.features.items()},
                "score": self.score.as_dict()}  # fmt: skip

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Reference":
        def bins(d: dict[str, list[float]]) -> Bins:
            return Bins(tuple(d["edges"]), tuple(d["shares"]))

        return cls({k: bins(v) for k, v in data["features"].items()}, bins(data["score"]))


@dataclass(frozen=True)
class Champion:
    """The model serving predictions, with what every prediction must record."""

    model: ClassifierMixin
    name: str
    version: str
    threshold: float
    reference: Reference


def git_sha() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                             check=True)  # fmt: skip
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def production_threshold(model: ClassifierMixin, data: pd.DataFrame) -> float:
    """The cut-off that alerts on as many visits as the rule engine did in training data."""
    scores = model.predict_proba(data[list(FEATURE_NAMES)])[:, 1]
    return threshold_for_rate(scores, float(data["rule_alert"].mean()))


def reference(model: ClassifierMixin, data: pd.DataFrame) -> Reference:
    scores = model.predict_proba(data[list(FEATURE_NAMES)])[:, 1]
    return Reference({f: reference_bins(data[f].to_numpy(dtype=float)) for f in FEATURE_NAMES},
                     reference_bins(scores))  # fmt: skip


def flat_metrics(summary: Summary) -> dict[str, float]:
    """Headline numbers as MLflow metrics."""
    out: dict[str, float] = {}
    for split, scores in (("internal", summary.internal), ("external", summary.external)):
        for kind, s in scores.items():
            prefix = f"{split}_{kind.value}"
            out |= {f"{prefix}_auroc": s.auroc.value, f"{prefix}_auprc": s.auprc.value,
                    f"{prefix}_brier": s.brier.value,
                    f"{prefix}_calibration_slope": s.calibration_slope}  # fmt: skip
    for split, b in (("internal", summary.burden_internal), ("external", summary.burden_external)):
        out |= {f"{split}_rule_sensitivity": b.rule_sensitivity.value,
                f"{split}_model_sensitivity_same_burden": b.model_sensitivity.value}  # fmt: skip
    return out


def register(
    development: Development,
    summary: Summary,
    production: ClassifierMixin,
    frame: pd.DataFrame,
    card: Callable[[str], str],
    extract_date: date,
    tracking_uri: str,
) -> str:
    """Log the run, register the production model, move the champion alias. Returns the version.

    `card(version)` renders the model card once the registry has assigned the version."""
    data = labelled(frame, development.label)
    threshold = production_threshold(production, data)
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(EXPERIMENT)
    with (
        mlflow.start_run(run_name=f"risk-{extract_date.isoformat()}") as run,
        TemporaryDirectory() as tmp,
    ):
        mlflow.set_tags({"label": LABEL_TEXT, "intended_use": INTENDED_USE, "git_sha": git_sha(),
                         "data_extract_date": extract_date.isoformat(),
                         "champion": development.champion.value})  # fmt: skip
        mlflow.log_params({
            "label": development.label, "horizon_days": 90, "features": ",".join(FEATURE_NAMES),
            "champion": development.champion.value, "alert_threshold": round(threshold, 6),
            "train_visits": len(data), "train_patients": data["key"].nunique(),
            "train_events": int(data[development.label].sum()),
            **{f"{k.value}_{p}": v for k, params in development.best.items()
               for p, v in params.items()},
        })  # fmt: skip
        for r in development.cv:
            tag = "_".join(f"{k}{v}" for k, v in r.params.items())
            mlflow.log_metric(f"cv_auprc_{r.kind.value}_{tag}", r.auprc)
        mlflow.log_metrics(flat_metrics(summary))
        folder = Path(tmp)
        (folder / "evaluation.json").write_text(json.dumps(summary.as_dict(), indent=2))
        (folder / REFERENCE_FILE).write_text(json.dumps(reference(production, data).as_dict()))
        mlflow.log_artifacts(str(folder))
        sample = data[list(FEATURE_NAMES)].head(5)
        info = mlflow.sklearn.log_model(
            production, name="model", registered_model_name=MODEL_NAME,
            signature=infer_signature(sample, production.predict_proba(sample)[:, 1]),
            serialization_format="cloudpickle",
        )  # fmt: skip
        version = str(info.registered_model_version)
        (folder / CARD_FILE).write_text(card(version))
        mlflow.log_artifact(str(folder / CARD_FILE))
    client = MlflowClient()
    for key, value in {
        "alert_threshold": str(threshold),
        "run_id": run.info.run_id,
        "label": LABEL_TEXT,
        "champion_kind": development.champion.value,
    }.items():
        client.set_model_version_tag(MODEL_NAME, version, key, value)
    client.set_registered_model_alias(MODEL_NAME, ALIAS, version)
    return version


def load_champion(tracking_uri: str) -> Champion:
    """The model behind the `champion` alias with its threshold and drift reference."""
    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient()
    mv = client.get_model_version_by_alias(MODEL_NAME, ALIAS)
    model = mlflow.sklearn.load_model(f"models:/{MODEL_NAME}@{ALIAS}")
    path = mlflow.artifacts.download_artifacts(run_id=mv.run_id, artifact_path=REFERENCE_FILE)
    ref = Reference.from_dict(json.loads(Path(path).read_text()))
    return Champion(model, MODEL_NAME, str(mv.version), float(mv.tags["alert_threshold"]), ref)


@lru_cache(maxsize=4)
def cached_champion(tracking_uri: str, version: str) -> Champion:
    """Loaded once per (registry, version), so a new champion is picked up without a restart."""
    champion = load_champion(tracking_uri)
    if champion.version != version:  # the alias moved while loading: try again next call
        cached_champion.cache_clear()
    return champion


def champion_version(tracking_uri: str) -> str | None:
    """The version the alias points at now (None if nothing is registered yet)."""
    mlflow.set_tracking_uri(tracking_uri)
    try:
        return str(MlflowClient().get_model_version_by_alias(MODEL_NAME, ALIAS).version)
    except mlflow.exceptions.MlflowException:
        return None
