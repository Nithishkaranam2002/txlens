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
from txlens_omop.check import (
    check_codes,
    check_drug_names,
    check_local_concept,
    check_mapping,
    format_local_report,
    format_report,
    suggest_ingredients,
)
from txlens_omop.codes import CodeUse
from txlens_omop.loader import (
    MissingVocabularyError,
    load_local_only,
    load_vocabulary,
    vocab_table_sizes,
)
from txlens_omop.mappings import LOCAL_CONCEPT_FILE, MappingRow, mappings_dir, read_mappings
from txlens_omop.vocab import (
    descendants,
    find_concept,
    get_concept,
    ingredients_for_name,
    standard_concepts,
)

pytestmark = pytest.mark.db
FAKE_ATHENA = Path(__file__).parents[2] / "omop" / "tests" / "fixtures" / "athena_fake"
B = 2_100_000_000
Conn = psycopg.Connection[tuple[object, ...]]
FAKE_CONCEPTS = 18  # TEST ONLY concepts in the fake Athena download
# Our local concepts ship with the package and are loaded with every vocabulary.
LOCAL_CONCEPTS = len((mappings_dir() / LOCAL_CONCEPT_FILE).read_text().splitlines()) - 1


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
        "SNOMED": 8, "ICD10CM": 3, "LOINC": 2, "RxNorm": 4, "Gender": 1,
    }  # fmt: skip
    assert "CPT4" not in report.concepts_by_vocabulary
    assert "NDC" not in report.concepts_by_vocabulary
    assert any("UCUM" in w and "unit_concept_id = 0" in w for w in report.warnings)
    # NDC "Maps to" rows are dropped with the NDC concept; ancestors of CPT4 too.
    assert report.rows["concept_relationship"] == 26
    assert report.rows["concept_ancestor"] == 21
    assert report.rows["concept_synonym"] == 2
    assert report.rows["local_concept"] == LOCAL_CONCEPTS
    assert report.rows["source_to_concept_map"] == len(read_mappings())


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
    assert (
        count(loaded, "SELECT count(*) FROM vocab.concept")
        == before
        == FAKE_CONCEPTS + LOCAL_CONCEPTS
    )
    stcm = count(loaded, "SELECT count(*) FROM vocab.source_to_concept_map")
    assert stcm == len(read_mappings())


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


def use(vocabulary: str, code: str, domain: str) -> CodeUse:
    return CodeUse(vocabulary, code, "test label", domain, "test")


def test_code_check_flags_each_kind_of_problem(loaded: Conn) -> None:
    results = check_codes(
        loaded,
        [
            use("LOINC", "TEST-LN-2160", "Measurement"),
            use("ICD10CM", "TEST-T86.11", "Condition"),
            use("LOINC", "TEST-LN-OBS", "Measurement"),
            use("RxNorm", "NOPE", "Drug"),
            use("ICD10CM", "TEST-Z99.99", "Condition"),
            use("SNOMED", "TEST-SCT-999", "Condition"),
        ],
    )
    statuses = [r.status for r in results]
    assert statuses[:2] == ["OK", "OK"]
    assert statuses[2].startswith("WRONG DOMAIN (Observation")
    assert statuses[3] == "NOT FOUND"
    assert "NO STANDARD MAPPING" in statuses[4]
    assert statuses[5] == "DEPRECATED (U)"
    report = format_report(results)
    assert "maps to: SNOMED TEST-SCT-101" in report
    assert "2 OK, 4 with problems, 6 checked." in report


def test_code_check_verifies_the_expected_ancestor(loaded: Conn) -> None:
    under = CodeUse("SNOMED", "TEST-SCT-102", "x", "Condition", "test", ("SNOMED", "TEST-SCT-100"))
    itself = CodeUse("SNOMED", "TEST-SCT-100", "x", "Condition", "test", ("SNOMED", "TEST-SCT-100"))
    sibling = CodeUse(
        "SNOMED", "TEST-SCT-200", "x", "Condition", "test", ("SNOMED", "TEST-SCT-100")
    )
    no_parent = CodeUse("SNOMED", "TEST-SCT-102", "x", "Condition", "test", ("SNOMED", "NOPE"))
    statuses = [r.status for r in check_codes(loaded, [under, itself, sibling, no_parent])]
    assert statuses == ["OK", "OK", "NOT UNDER SNOMED TEST-SCT-100", "NOT UNDER SNOMED NOPE"]


def test_ingredient_suggestions_for_uncoded_drugs(loaded: Conn) -> None:
    assert [c.concept_code for c in suggest_ingredients(loaded, "tacrolimus")] == ["TEST-RX-42316"]


def test_local_concept_is_loaded_as_non_standard(loaded: Conn) -> None:
    concept = get_concept(loaded, 2_000_000_001)
    assert concept is not None
    assert (concept.vocabulary_id, concept.domain_id) == ("TxLens", "Measurement")
    assert not concept.is_standard


def ingredient_codes(conn: Conn, name: str) -> list[str]:
    return [c.concept_code for c in ingredients_for_name(conn, name)]


def test_drug_name_by_exact_name_synonym_and_brand(loaded: Conn) -> None:
    assert ingredient_codes(loaded, "TEST ONLY - Tacrolimus") == ["TEST-RX-42316"]
    assert ingredient_codes(loaded, "  test only - fk-TEST ") == ["TEST-RX-42316"]  # synonym
    assert ingredient_codes(loaded, "TEST ONLY - BRANDOLIMUS") == ["TEST-RX-42316"]  # brand
    assert ingredient_codes(loaded, "TEST ONLY - Tacro") == []  # no substring matching


def test_reviewed_mapping_wins_over_name_match(loaded: Conn) -> None:
    with loaded.transaction(force_rollback=True):
        loaded.execute(
            "INSERT INTO vocab.source_to_concept_map VALUES "
            "('test only - tacrolimus', 0, 'TXLENS_DRUG', 'test', %s, 'RxNorm', "
            "'2026-01-01', '2099-12-31', NULL)",
            (B + 31,),
        )
        assert ingredient_codes(loaded, "TEST ONLY - Tacrolimus") == ["TEST-RX-1001"]


def test_drug_name_check_reports_names_that_do_not_resolve(loaded: Conn) -> None:
    names = {"tacrolimus": ("TEST ONLY - Brandolimus", "tacro")}
    problems = check_drug_names(loaded, names, {"tacrolimus": "TEST-RX-42316"})
    assert problems == [("tacrolimus", "tacro", "resolves to nothing")]


def test_mapping_check_flags_each_problem(loaded: Conn) -> None:
    def row(vocabulary: str, target: int, target_vocabulary: str) -> MappingRow:
        return MappingRow("x", vocabulary, "test", target, target_vocabulary)

    good = check_mapping(loaded, row("TXLENS_LAB_NAME", B + 20, "LOINC"))
    local = check_mapping(loaded, row("TXLENS_LAB_NAME", 2_000_000_001, "TxLens"))
    missing = check_mapping(loaded, row("TXLENS_LAB_NAME", 42, "LOINC"))
    wrong = check_mapping(loaded, row("TXLENS_DRUG", B + 10, "SNOMED"))
    assert good.problems == () and local.problems == ()
    assert missing.problems == ("TARGET NOT FOUND",)
    assert "VOCABULARY IS ICD10CM, ROW SAYS SNOMED" in wrong.problems
    assert "WRONG DOMAIN (Condition, expected Drug)" in wrong.problems
    assert "TARGET NOT STANDARD" in wrong.problems


def test_condition_mapping_may_target_a_concept_that_maps_to_one_standard(loaded: Conn) -> None:
    def row(vocabulary: str) -> MappingRow:
        return MappingRow("x", vocabulary, "test", B + 10, "ICD10CM")

    assert check_mapping(loaded, row("TXLENS_CONDITION")).problems == ()
    assert "TARGET NOT STANDARD" in check_mapping(loaded, row("TXLENS_PROCEDURE")).problems


def test_local_concept_check_flags_each_problem(loaded: Conn) -> None:
    with loaded.transaction(force_rollback=True):
        loaded.execute(
            "UPDATE vocab.concept SET standard_concept = 'S' WHERE concept_code = 'BANFF_T' "
            "AND vocabulary_id = 'TxLens'"
        )
        results = [
            check_local_concept(loaded, "BANFF_I", "Measurement"),
            check_local_concept(loaded, "BANFF_I", "Condition"),
            check_local_concept(loaded, "BANFF_T", "Measurement"),
            check_local_concept(loaded, "NOPE", "Measurement"),
        ]
    assert [r.problems for r in results] == [
        (),
        ("WRONG DOMAIN (Measurement, expected Condition)",),
        ("MARKED STANDARD",),
        ("NOT FOUND",),
    ]
    assert "1 OK, 3 with problems, 4 checked." in format_local_report(results)


def test_load_local_only_replaces_our_rows_and_keeps_athena(loaded: Conn) -> None:
    with loaded.transaction(force_rollback=True):
        athena = count(loaded, "SELECT count(*) FROM vocab.concept WHERE vocabulary_id <> 'TxLens'")
        loaded.execute("DELETE FROM vocab.concept WHERE concept_code = 'BANFF_I'")
        rows = load_local_only(loaded)
        assert rows["local_concept"] == count(
            loaded, "SELECT count(*) FROM vocab.concept WHERE vocabulary_id = 'TxLens'"
        )
        assert rows["source_to_concept_map"] == len(read_mappings())
        assert find_concept(loaded, "TxLens", "BANFF_I") is not None
        assert athena == count(
            loaded, "SELECT count(*) FROM vocab.concept WHERE vocabulary_id <> 'TxLens'"
        )
