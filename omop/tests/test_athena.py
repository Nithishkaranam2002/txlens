from pathlib import Path

import pytest

from txlens_omop.athena import (
    EXCLUDED_VOCABULARIES,
    REQUIRED_VOCABULARIES,
    VOCABULARIES,
    AthenaSource,
    wanted_vocabularies,
)

B = 2_100_000_000


def test_vocabulary_filter_includes_icd10cm_and_excludes_cpt4() -> None:
    wanted = wanted_vocabularies()
    assert {"SNOMED", "LOINC", "RxNorm", "ICD10CM", "UCUM"} <= wanted
    assert "CPT4" not in wanted
    assert "CPT4" not in wanted_vocabularies(VOCABULARIES | {"CPT4"})
    assert "CPT4" in EXCLUDED_VOCABULARIES
    assert wanted >= REQUIRED_VOCABULARIES


@pytest.mark.parametrize("kind", ["dir", "zip"])
def test_rows_stream_from_folder_and_zip(kind: str, fake_dir: Path, fake_zip: Path) -> None:
    source = AthenaSource(fake_dir if kind == "dir" else fake_zip)
    with source.rows("concept") as (header, lines):
        rows = list(lines)
    assert header[:3] == ["concept_id", "concept_name", "domain_id"]
    assert rows[0].startswith(f"{B + 1}\t".encode())
    assert all(row.endswith(b"\n") for row in rows)
    assert len(rows) == 20


def test_find_picks_the_zip_or_the_folder(fake_dir: Path, fake_zip: Path) -> None:
    assert AthenaSource.find(fake_zip.parent).path == fake_zip
    assert AthenaSource.find(fake_dir).path == fake_dir


def test_find_fails_without_a_download(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        AthenaSource.find(tmp_path)


def test_missing_file_is_reported(fake_zip: Path) -> None:
    with (
        pytest.raises(FileNotFoundError, match="DRUG_STRENGTH"),
        AthenaSource(fake_zip).open("DRUG_STRENGTH.csv"),
    ):
        pass
