"""infra/demo/relocate_mlruns.py: a snapshot's MLflow store restored in another folder points
at the new folder, in the database and the model files, and nothing else changes."""

import importlib.util
import sqlite3
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).parents[2] / "infra" / "demo" / "relocate_mlruns.py"
OLD, NEW = "/Users/someone/txlens/mlruns", "/opt/txlens/mlruns"


def load_script() -> ModuleType:
    """The script is not part of a package (restore.sh runs it with the host's python3)."""
    spec = importlib.util.spec_from_file_location("relocate_mlruns", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_store(folder: Path) -> None:
    """A tiny store shaped like MLflow's: absolute paths in text columns and model files."""
    with sqlite3.connect(folder / "mlflow.db") as conn:
        conn.execute("CREATE TABLE runs (run_uuid VARCHAR(32), artifact_uri VARCHAR(200), n INT)")
        conn.execute("CREATE TABLE model_versions (source VARCHAR(500), storage_location TEXT)")
        conn.execute("INSERT INTO runs VALUES ('r1', ?, 1)", (f"{OLD}/1/r1/artifacts",))
        conn.execute("INSERT INTO runs VALUES ('r2', 's3://elsewhere/artifacts', 2)")
        conn.execute("INSERT INTO model_versions VALUES ('models:/m-1', ?)", (f"{OLD}/1/m-1",))
    model = folder / "1" / "models" / "m-1" / "artifacts"
    model.mkdir(parents=True)
    (model / "MLmodel").write_text(f"artifact_path: {OLD}/1/models/m-1/artifacts\n")
    (folder / "1" / "meta.yaml").write_text(f"artifact_location: {OLD}/1\n")
    (folder / "1" / "notes.txt").write_text(f"{OLD} is left alone\n")


def test_relocate_rewrites_database_and_model_files(tmp_path: Path) -> None:
    make_store(tmp_path)
    rows, files = load_script().relocate(tmp_path, OLD, NEW + "/")
    with sqlite3.connect(tmp_path / "mlflow.db") as conn:
        runs = conn.execute("SELECT artifact_uri FROM runs ORDER BY run_uuid").fetchall()
        model = conn.execute("SELECT source, storage_location FROM model_versions").fetchone()
    assert (rows, files) == (2, 2)
    assert runs == [(f"{NEW}/1/r1/artifacts",), ("s3://elsewhere/artifacts",)]
    assert model == ("models:/m-1", f"{NEW}/1/m-1")
    assert NEW in (tmp_path / "1" / "models" / "m-1" / "artifacts" / "MLmodel").read_text()
    assert (tmp_path / "1" / "meta.yaml").read_text() == f"artifact_location: {NEW}/1\n"
    assert OLD in (tmp_path / "1" / "notes.txt").read_text()


def test_relocate_to_the_same_folder_changes_nothing(tmp_path: Path) -> None:
    make_store(tmp_path)
    assert load_script().relocate(tmp_path, OLD, OLD + "/") == (0, 0)
