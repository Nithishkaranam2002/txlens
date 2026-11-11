import json
from pathlib import Path

import pytest

from txlens_datagen.cli import main


def test_cli_writes_data_and_summary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--n-patients", "5", "--out", str(tmp_path), "--no-pdfs"]) == 0
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["n_patients"] == 5
    assert manifest["pdfs"] is False
    extract = json.loads((tmp_path / "raw" / "extract.json").read_text())
    assert extract == {"extract_date": manifest["data_cutoff"]}
    out = capsys.readouterr().out
    assert "Patients by center/organ" in out
    assert "Total size on disk" in out


def test_cli_can_skip_note_text(tmp_path: Path) -> None:
    assert main(["--n-patients", "5", "--out", str(tmp_path), "--no-pdfs", "--no-note-text"]) == 0
    assert not (tmp_path / "raw" / "notes").exists()
    assert (tmp_path / "ground_truth" / "labs.csv").exists()


def test_rerun_replaces_previous_output(tmp_path: Path) -> None:
    main(["--n-patients", "8", "--out", str(tmp_path), "--no-pdfs"])
    main(["--n-patients", "3", "--out", str(tmp_path), "--no-pdfs"])
    assert len(list((tmp_path / "raw" / "fhir").rglob("*.json"))) == 3
