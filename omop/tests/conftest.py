"""Fixtures for the OMOP package tests."""

import zipfile
from pathlib import Path

import pytest

FAKE_ATHENA = Path(__file__).parent / "fixtures" / "athena_fake"


@pytest.fixture
def fake_dir() -> Path:
    """The fake Athena fixture as a folder of CSVs."""
    return FAKE_ATHENA


@pytest.fixture
def fake_zip(tmp_path: Path) -> Path:
    """The fake fixture packed like an Athena download (files inside a sub-folder)."""
    path = tmp_path / "vocabulary_download_v5_test.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for csv_file in FAKE_ATHENA.glob("*.csv"):
            archive.write(csv_file, f"download/{csv_file.name}")
    return path
