"""`txlens-risk train`: build the visit dataset, develop, register a new version, write the card.

Reads every center as the RLS-bound app login, one tenant transaction at a time, and pools
the visits without identifiers beyond an internal patient key (ADR 0019 records why pooling
centers is a governance decision).
"""

import argparse
from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine

from txlens_backend.config import get_settings
from txlens_backend.db import check_role_is_rls_bound, make_engine, tenant_transaction
from txlens_backend.db_setup import center_tenants
from txlens_backend.risk import card, dataset
from txlens_backend.risk.develop import develop, production_model
from txlens_backend.risk.evaluate import summarize
from txlens_backend.risk.registry import register
from txlens_backend.surveillance.run import transplant_configs

CARD_PATH = Path("docs/model_card_risk.md")


def all_visits(engine: Engine, as_of: date) -> pd.DataFrame:
    """Every center's surveillance visits (RLS on, one center at a time)."""
    frames = []
    for tenant in center_tenants():
        with tenant_transaction(engine, tenant) as conn:
            frames.append(dataset.build(conn, tenant, transplant_configs(), as_of))
    return pd.concat(frames, ignore_index=True)


def train(as_of: date, card_path: Path = CARD_PATH) -> str:
    """Develop, evaluate and register; returns the new version."""
    settings = get_settings()
    engine = make_engine(settings.app_database_url.get_secret_value())
    check_role_is_rls_bound(engine)
    frame = all_visits(engine, as_of)
    development = develop(frame)
    summary = summarize(development)
    production = production_model(development, frame)

    def render(version: str) -> str:
        text = card.render(development, summary, frame, version, as_of)
        card_path.write_text(text)
        return text

    return register(development, summary, production, frame, render, as_of,
                    settings.mlflow_tracking_uri)  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="txlens-risk", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    t = sub.add_parser("train", help="train, evaluate and register a new model version")
    t.add_argument("--as-of", type=date.fromisoformat, required=True,
                   help="data extract date (YYYY-MM-DD)")  # fmt: skip
    args = parser.parse_args(argv)
    version = train(args.as_of)
    print(f"Registered version {version} as champion; model card: {CARD_PATH}")
    return 0
