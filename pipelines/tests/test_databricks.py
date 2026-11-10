"""Databricks entry points: volume layout, unpacking, layer guard, notebook syntax."""

import ast
import zipfile
from pathlib import Path
from typing import cast

import pytest

pytest.importorskip("pyspark", reason="the Databricks module imports PySpark")

from pyspark.sql import SparkSession

from txlens_pipelines.databricks import VolumePaths, run_pipeline, unpack

NOTEBOOKS = Path(__file__).parents[1] / "databricks" / "notebooks"


def test_volume_paths() -> None:
    paths = VolumePaths("workspace")
    assert paths.raw == "/Volumes/workspace/txlens/data/raw"
    assert paths.vocab == "/Volumes/workspace/txlens/data/vocab_subset"


def test_unpack_extracts_the_uploaded_zip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    upload = tmp_path / "upload.zip"
    with zipfile.ZipFile(upload, "w") as archive:
        for folder in ("raw", "ground_truth", "vocab_subset"):
            archive.writestr(f"{folder}/x.txt", "x")
    monkeypatch.setattr(VolumePaths, "root", property(lambda _: str(tmp_path / "volume")))
    assert unpack(str(upload), VolumePaths("workspace")) == ["ground_truth", "raw", "vocab_subset"]


def test_postgres_load_is_refused_on_databricks() -> None:
    with pytest.raises(ValueError, match="locally"):
        run_pipeline(cast(SparkSession, None), "workspace", ["gold", "load"])


@pytest.mark.parametrize("notebook", sorted(NOTEBOOKS.glob("*.py")), ids=lambda p: p.name)
def test_notebooks_are_databricks_source_files_and_valid_python(notebook: Path) -> None:
    text = notebook.read_text()
    assert text.startswith("# Databricks notebook source\n")
    ast.parse(text)
