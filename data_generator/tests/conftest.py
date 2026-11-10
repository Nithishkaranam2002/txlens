"""Shared fixtures: one in-memory cohort reused by the statistical tests."""

import pytest

from txlens_datagen.config import GeneratorConfig
from txlens_datagen.models import PatientRecord
from txlens_datagen.record import build_record

COHORT_SIZE = 2000


@pytest.fixture(scope="session")
def config() -> GeneratorConfig:
    return GeneratorConfig()


@pytest.fixture(scope="session")
def cohort(config: GeneratorConfig) -> list[PatientRecord]:
    """2000 patients built in memory (no files, no note text) for rate and range checks."""
    return [build_record(i, config, with_notes=False) for i in range(COHORT_SIZE)]
