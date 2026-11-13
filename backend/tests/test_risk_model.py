"""Risk model metrics, explanations, patient-level splits, the champion rule and the registry."""

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from risk_testutils import visits

from txlens_backend.risk import LABEL_TEXT
from txlens_backend.risk.card import render
from txlens_backend.risk.develop import (
    CHAMPION_MARGIN,
    EXTERNAL_CENTER,
    TRAIN_CENTERS,
    CvResult,
    champion,
    develop,
    production_model,
    score_column,
)
from txlens_backend.risk.evaluate import age_band, summarize
from txlens_backend.risk.explain import contributions, top_contributions
from txlens_backend.risk.features import FEATURE_NAMES
from txlens_backend.risk.metrics import (
    Bins,
    calibration,
    cluster_bootstrap,
    net_benefit,
    psi,
    psi_against,
    reference_bins,
    threshold_for_rate,
)
from txlens_backend.risk.models import Kind
from txlens_backend.risk.registry import (
    MODEL_NAME,
    champion_version,
    latest_active_visits,
    load_champion,
    register,
)
from txlens_backend.risk.serve import feature_psi, score, score_psi


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return visits()


@pytest.fixture(scope="module")
def development(frame: pd.DataFrame):  # type: ignore[no-untyped-def]
    return develop(frame)


# --- metrics ----------------------------------------------------------------------------


def test_calibration_of_well_calibrated_scores() -> None:
    rng = np.random.default_rng(1)
    p = rng.uniform(0.01, 0.5, 50_000)
    y = (rng.random(50_000) < p).astype(float)
    intercept, slope = calibration(y, p)
    assert intercept == pytest.approx(0, abs=0.05) and slope == pytest.approx(1, abs=0.05)
    assert calibration(y, np.clip(p * 2, 0, 0.99))[0] < -0.3  # over-predicting: negative


def test_net_benefit_and_alert_threshold() -> None:
    y = np.array([1, 0, 0, 1, 0], dtype=float)
    alert = np.array([True, True, False, False, False])
    assert net_benefit(y, alert, 0.2) == pytest.approx(1 / 5 - 1 / 5 * 0.25)
    p = np.linspace(0, 1, 101)
    assert (p >= threshold_for_rate(p, 0.1)).mean() == pytest.approx(0.1, abs=0.011)


def test_bootstrap_resamples_patients_and_is_repeatable() -> None:
    keys = [f"p{i // 4}" for i in range(400)]
    x = np.arange(400, dtype=float)
    first = cluster_bootstrap(keys, [x], lambda a: float(a.mean()), resamples=200)
    again = cluster_bootstrap(keys, [x], lambda a: float(a.mean()), resamples=200)
    assert first == again and first.low < first.value < first.high


def test_psi() -> None:
    rng = np.random.default_rng(2)
    base = rng.normal(0, 1, 20_000)
    assert psi(base, rng.normal(0, 1, 20_000)) < 0.01
    assert psi(base, rng.normal(1, 1, 20_000)) > 0.25
    with_missing = np.where(rng.random(20_000) < 0.5, np.nan, base)
    assert psi(base, with_missing) > 0.25  # missingness itself is a shift
    flags = (rng.random(20_000) < 0.05).astype(float)
    assert psi(flags, (rng.random(20_000) < 0.30).astype(float)) > 0.25  # yes/no feature
    stored = reference_bins(base)
    assert psi_against(Bins(stored.edges, stored.shares), base) == pytest.approx(0, abs=1e-9)


def test_age_bands() -> None:
    assert [age_band(a) for a in (18, 39, 40, 59, 60, 76)] == [
        "18-39", "18-39", "40-59", "40-59", "60+", "60+",
    ]  # fmt: skip


# --- splits and the champion rule -------------------------------------------------------


def test_no_patient_is_in_both_development_and_a_test_set(development) -> None:  # type: ignore[no-untyped-def]
    dev, internal, external = development.development, development.internal, development.external
    assert set(dev["key"]).isdisjoint(internal["key"])
    assert set(dev["tenant"]) | set(internal["tenant"]) <= set(TRAIN_CENTERS)
    assert set(external["tenant"]) == {EXTERNAL_CENTER}
    assert set(external["key"]).isdisjoint(set(dev["key"]) | set(internal["key"]))
    assert (
        0.15 < internal["key"].nunique() / (dev["key"].nunique() + internal["key"].nunique()) < 0.25
    )


def test_every_test_row_is_scored_by_both_families(development) -> None:  # type: ignore[no-untyped-def]
    for part in (development.development, development.internal, development.external):
        for kind in Kind:
            scores = part[score_column(kind)]
            assert scores.notna().all() and scores.between(0, 1).all()


def cv(kind: Kind, auprc: float) -> CvResult:
    return CvResult(kind, {}, auprc, 0.8)


def test_gradient_boosting_must_beat_the_baseline_by_the_margin() -> None:
    near = [cv(Kind.LOGISTIC, 0.30), cv(Kind.LIGHTGBM, 0.30 + CHAMPION_MARGIN / 2)]
    clear = [cv(Kind.LOGISTIC, 0.30), cv(Kind.LIGHTGBM, 0.30 + CHAMPION_MARGIN * 2)]
    assert champion(near) is Kind.LOGISTIC
    assert champion(clear) is Kind.LIGHTGBM


def test_the_model_learns_the_planted_signal(development) -> None:  # type: ignore[no-untyped-def]
    s = summarize(development)
    assert s.external[Kind.LOGISTIC].auroc.value > 0.7
    assert s.burden_external.alert_rate == pytest.approx(development.external["rule_alert"].mean())


# --- explanations -----------------------------------------------------------------------


@pytest.mark.parametrize("kind", list(Kind))
def test_contributions_add_up_to_the_logit(development, frame: pd.DataFrame, kind: Kind) -> None:  # type: ignore[no-untyped-def]
    model = development.models[f"external_{kind.value}"]
    rows = frame.head(20)
    values, base = contributions(model, rows)
    p = model.predict_proba(rows[list(FEATURE_NAMES)])[:, 1]
    assert values.sum(axis=1) + base == pytest.approx(np.log(p / (1 - p)), abs=1e-6)
    top = top_contributions(model, rows.head(1), k=3)
    assert len(top) == 3
    assert abs(top[0].log_odds) >= abs(top[1].log_odds) >= abs(top[2].log_odds)


# --- registry and model card ------------------------------------------------------------


def test_register_load_and_promote(development, frame: pd.DataFrame, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    assert champion_version(uri) is None
    summary = summarize(development)
    production = production_model(development, frame)
    cards: list[str] = []

    def card(version: str) -> str:
        cards.append(render(development, summary, frame, version, date(2026, 9, 30)))
        return cards[-1]

    first = register(development, summary, production, frame, card, date(2026, 9, 30), uri)
    second = register(development, summary, production, frame, card, date(2026, 9, 30), uri)
    assert (first, second) == ("1", "2") and champion_version(uri) == "2"
    loaded = load_champion(uri, "2")
    assert (loaded.name, loaded.version) == (MODEL_NAME, "2")
    rows = frame[list(FEATURE_NAMES)].head(5)
    assert loaded.model.predict_proba(rows)[:, 1] == pytest.approx(
        production.predict_proba(rows)[:, 1]
    )
    alert_share = (production.predict_proba(frame[list(FEATURE_NAMES)])[:, 1]
                   >= loaded.threshold).mean()  # fmt: skip
    assert alert_share == pytest.approx(frame["rule_alert"].mean(), abs=0.01)
    assert set(loaded.reference.features) == set(FEATURE_NAMES)
    assert f"{MODEL_NAME} v2" in cards[-1] and LABEL_TEXT in cards[-1]
    # nightly scoring: version on every prediction, drift against the stored reference
    predictions = score(loaded, frame.groupby("key").tail(1))
    assert {(p.model_name, p.model_version) for p in predictions} == {(MODEL_NAME, "2")}
    assert all(len(p.contributions) == 3 for p in predictions)
    assert set(feature_psi(loaded, predictions)) == set(FEATURE_NAMES)
    psi_now = score_psi(loaded, predictions)
    assert psi_now is not None and psi_now < 0.1  # same population as the reference


def test_drift_reference_is_the_nightly_population() -> None:
    frame = pd.DataFrame({
        "key": ["a", "a", "b", "b", "c"],
        "visit": [date(2024, 1, 1), date(2024, 4, 1), date(2024, 2, 1), date(2024, 1, 1),
                  date(2024, 3, 1)],
        "active": [True, True, True, True, False],
    })  # fmt: skip
    latest = latest_active_visits(frame)
    assert sorted(zip(latest["key"], latest["visit"], strict=True)) == [
        ("a", date(2024, 4, 1)), ("b", date(2024, 2, 1))
    ]  # fmt: skip
