"""Vocabulary loader and lookups, against the fake Athena fixture (TEST ONLY concepts)."""

import shutil
import zipfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import psycopg
import pytest
from db_testutils import create_database, drop_database, migrate

from txlens_backend.db_setup import libpq_url
from txlens_omop.athena import AthenaSource
from txlens_omop.loader import MissingVocabularyError, load_vocabulary, vocab_table_sizes
from txlens_omop.vocab import descendants, find_concept, standard_concepts

pytestmark = pytest.mark.db
FAKE_ATHENA = Path(__file__).parents[2] / "omop" / "tests" / "fixtures" / "athena_fake"
B = 2_100_000_000
Conn = psycopg.Connection[tuple[object, ...]]


@pytest.fixture(scope="module")
def vocab_db() -> Iterator[str]:
    """A separate migrated database for vocabulary loads."""
    url = create_database("vocab")
    try:
        migrate(url)
        yield libpq_url(url)
    finally:
        drop_database(url)


@pytest.fixture(scope="module")
def fake_zip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("athena") / "vocabulary_download_v5_test.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for csv_file in FAKE_ATHENA.glob("*.csv"):
            archive.write(csv_file, csv_file.name)
    return path


@pytest.fixture(scope="module")
def loaded(vocab_db: str, fake_zip: Path) -> Iterator[Conn]:
    """The fake vocabulary loaded from the zip; yields an open connection."""
    with psycopg.connect(vocab_db) as conn:
        load_vocabulary(conn, AthenaSource(fake_zip))
        yield conn


def count(conn: Conn, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    assert row is not None
    return int(str(row[0]))


def test_load_filters_vocabularies_and_warns_about_ucum(vocab_db: str, fake_zip: Path) -> None:
    with psycopg.connect(vocab_db) as conn:
        report = load_vocabulary(conn, AthenaSource(fake_zip))
    assert report.concepts_by_vocabulary == {
        "SNOMED": 8, "ICD10CM": 3, "LOINC": 2, "RxNorm": 3, "Gender": 1,
    }  # fmt: skip
    assert "CPT4" not in report.concepts_by_vocabulary
    assert "NDC" not in report.concepts_by_vocabulary
    assert any("UCUM" in w and "unit_concept_id = 0" in w for w in report.warnings)
    # NDC "Maps to" rows are dropped with the NDC concept; ancestors of CPT4 too.
    assert report.rows["concept_relationship"] == 24
    assert report.rows["concept_ancestor"] == 21
    assert report.rows["concept_synonym"] == 1


def test_loaded_rows_have_parsed_dates_and_nulls(loaded: Conn) -> None:
    row = loaded.execute(
        "SELECT valid_start_date, valid_end_date, invalid_reason, standard_concept "
        "FROM vocab.concept WHERE concept_id = %s",
        (B + 10,),
    ).fetchone()
    assert row == (date(1970, 1, 1), date(2099, 12, 31), None, None)


def test_reload_replaces_rather_than_duplicates(loaded: Conn, fake_zip: Path) -> None:
    before = count(loaded, "SELECT count(*) FROM vocab.concept")
    load_vocabulary(loaded, AthenaSource(fake_zip))
    assert count(loaded, "SELECT count(*) FROM vocab.concept") == before == 17


def test_indexes_are_rebuilt_after_load(loaded: Conn) -> None:
    names = {
        r[0] for r in loaded.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'vocab'")
    }
    assert {"idx_concept_code", "idx_concept_ancestor_id_2", "ix_concept_vocabulary_code"} <= names
    assert vocab_table_sizes(loaded)["concept"] > 0


def test_missing_required_vocabulary_fails_and_keeps_old_data(loaded: Conn, tmp_path: Path) -> None:
    broken = tmp_path / "no_snomed"
    shutil.copytree(FAKE_ATHENA, broken)
    concept = broken / "CONCEPT.csv"
    lines = concept.read_text().splitlines(keepends=True)
    concept.write_text("".join(line for line in lines if "\tSNOMED\t" not in line))
    with pytest.raises(MissingVocabularyError, match="SNOMED"):
        load_vocabulary(loaded, AthenaSource(broken))
    assert count(loaded, "SELECT count(*) FROM vocab.concept WHERE vocabulary_id = 'SNOMED'") == 8


def test_icd10cm_source_code_maps_to_standard_snomed(loaded: Conn) -> None:
    source = find_concept(loaded, "ICD10CM", "TEST-T86.11")
    assert source is not None and not source.is_standard
    [standard] = standard_concepts(loaded, "ICD10CM", "TEST-T86.11")
    assert (standard.concept_id, standard.vocabulary_id) == (B + 2, "SNOMED")
    assert standard.is_standard


def test_standard_code_maps_to_itself(loaded: Conn) -> None:
    [concept] = standard_concepts(loaded, "LOINC", "TEST-LN-2160")
    assert concept.concept_id == B + 20


def test_outdated_snomed_code_maps_to_replacement(loaded: Conn) -> None:
    assert [c.concept_id for c in standard_concepts(loaded, "SNOMED", "TEST-SCT-999")] == [B + 2]


def test_unmapped_and_unknown_codes_return_nothing(loaded: Conn) -> None:
    assert standard_concepts(loaded, "ICD10CM", "TEST-Z99.99") == []  # only a deprecated map
    assert standard_concepts(loaded, "ICD10CM", "NOT-A-CODE") == []


def test_ancestor_search_finds_all_rejection_subtypes(loaded: Conn) -> None:
    found = {c.concept_id for c in descendants(loaded, B + 1)}
    assert found == {B + 1, B + 2, B + 3, B + 4, B + 5}
    below = {c.concept_id for c in descendants(loaded, B + 1, include_self=False)}
    assert B + 1 not in below
    assert {c.concept_id for c in descendants(loaded, B + 6)} == {B + 6}
