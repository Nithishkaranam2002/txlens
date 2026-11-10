import hashlib
from dataclasses import replace
from pathlib import Path

from txlens_datagen.config import GeneratorConfig
from txlens_datagen.generate import generate
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


def file_hashes(root: Path) -> dict[str, str]:
    """SHA-256 of every file under root, keyed by relative path."""
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_same_seed_gives_identical_files(tmp_path: Path) -> None:
    config = GeneratorConfig(n_patients=15)
    generate(config, tmp_path / "one")
    generate(config, tmp_path / "two")
    assert file_hashes(tmp_path / "one") == file_hashes(tmp_path / "two")


def test_different_seed_gives_different_files(tmp_path: Path) -> None:
    generate(GeneratorConfig(n_patients=15), tmp_path / "one")
    generate(GeneratorConfig(n_patients=15, seed=7), tmp_path / "two")
    one, two = file_hashes(tmp_path / "one"), file_hashes(tmp_path / "two")
    assert one["ground_truth/labs.csv"] != two["ground_truth/labs.csv"]
