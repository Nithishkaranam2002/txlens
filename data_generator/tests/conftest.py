"""Shared fixtures: one in-memory cohort reused by the statistical tests."""

from dataclasses import replace
from pathlib import Path

import pytest

from txlens_datagen.config import GeneratorConfig
from txlens_datagen.generate import generate
from txlens_datagen.models import PatientRecord
from txlens_datagen.record import build_record

COHORT_SIZE = 2000
SMALL_N = 50


@pytest.fixture(scope="session")
def config() -> GeneratorConfig:
    return GeneratorConfig()


@pytest.fixture(scope="session")
def cohort(config: GeneratorConfig) -> list[PatientRecord]:
    """2000 patients built in memory (no files, no note text) for rate and range checks."""
    return [build_record(i, config, with_notes=False) for i in range(COHORT_SIZE)]


@pytest.fixture(scope="session")
def generated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A small generated dataset on disk (50 patients) for raw-vs-truth checks."""
    out = tmp_path_factory.mktemp("generated")
    generate(replace(GeneratorConfig(), n_patients=SMALL_N), out)
    return out
