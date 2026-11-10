from dataclasses import replace

from txlens_datagen.config import GeneratorConfig
from txlens_datagen.record import build_record


def test_same_seed_same_record() -> None:
    config = GeneratorConfig()
    assert build_record(7, config) == build_record(7, config)


def test_patient_does_not_depend_on_cohort_size() -> None:
    small = GeneratorConfig(n_patients=10)
    large = GeneratorConfig(n_patients=5000)
    assert build_record(3, small) == build_record(3, large)


def test_different_seed_different_record() -> None:
    config = GeneratorConfig()
    other = replace(config, seed=config.seed + 1)
    assert build_record(0, config).patient != build_record(0, other).patient
