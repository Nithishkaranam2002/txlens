import pytest

from txlens_datagen.cli import main


def test_cli_reports_not_built_yet(capsys: pytest.CaptureFixture[str]) -> None:
    assert main() == 1
    assert "Step 1.2" in capsys.readouterr().err
