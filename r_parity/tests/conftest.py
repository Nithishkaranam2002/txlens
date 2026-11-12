"""Fixtures for the R parity tests: the R image, and an OMOP extract from a throwaway database.

The tests need Docker with the R image (`make r-build`) and PostgreSQL; without them they skip
(CI's r-parity job sets TXLENS_REQUIRE_R=1 so that skipping fails instead).
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest
from api_dataset import build, load
from api_testutils import API_TENANTS
from db_testutils import create_database, drop_database, migrate
from sqlalchemy import create_engine, pool, text

from txlens_backend.survival.extract import extract
from txlens_evals.survival_parity import R_IMAGE, r_available

PARITY_PATIENTS = 1000  # enough graft losses for a five-covariate Cox model


@pytest.fixture(scope="session", autouse=True)
def r_image() -> None:
    """Skip (or, in CI, fail) without Docker and the R image."""
    if r_available():
        return
    message = f"Docker image {R_IMAGE} not found (run `make r-build`)"
    if os.environ.get("TXLENS_REQUIRE_R") == "1":
        pytest.fail(message)
    pytest.skip(message)


@pytest.fixture(scope="session")
def omop_extract(tmp_path_factory: pytest.TempPathFactory) -> Iterator[pd.DataFrame]:
    """Generated patients written into OMOP tables, then read back by the survival extract.

    Read as the database owner: the extract covers every center (RLS is tested elsewhere);
    what matters here is that R and Python get the same rows."""
    url = create_database("rparity")
    try:
        migrate(url)
        owner = create_engine(url, poolclass=pool.NullPool)
        with owner.begin() as conn:
            for tenant in API_TENANTS:
                conn.execute(text("INSERT INTO app.tenant (tenant_id, name) VALUES (:t, :t)"),
                             {"t": tenant})  # fmt: skip
            load(conn, build(tmp_path_factory.mktemp("parity_data"), PARITY_PATIENTS))
        with owner.connect() as conn:
            frame = extract(conn)
        owner.dispose()
        yield frame
    finally:
        drop_database(url)


@pytest.fixture
def work(tmp_path: Path) -> Path:
    """Folder shared with the R container."""
    return tmp_path
