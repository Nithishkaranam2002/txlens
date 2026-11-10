import pytest

from txlens_omop import ddl


@pytest.mark.parametrize("name", sorted(ddl.FILES.values()))
def test_pinned_files_are_unchanged(name: str) -> None:
    assert ddl.sha256(name) == ddl.checksums()[name]


def test_all_our_tables_are_found_in_the_right_schema() -> None:
    tables = ddl.tables()
    assert set(tables) == set(ddl.BASE_TABLES) | set(ddl.MAPPING_TABLES)
    for name in ddl.CLINICAL_TABLES:
        assert tables[name].schema == "cdm"
    for name in ddl.VOCAB_TABLES + ddl.MAPPING_TABLES:
        assert tables[name].schema == "vocab"


def test_columns_are_parsed() -> None:
    person = ddl.tables()["person"]
    assert len(person.columns) == 18
    first = person.columns[0]
    assert (first.name, first.data_type, first.nullable) == ("person_id", "integer", False)
    measurement = {c.name: c for c in ddl.tables()["measurement"].columns}
    assert measurement["value_as_number"].data_type == "numeric"
    assert measurement["value_as_number"].nullable
    assert "offset" in {c.name for c in ddl.tables()["note_nlp"].columns}
    concept = {c.name: c for c in ddl.tables()["concept"].columns}
    assert concept["concept_name"].data_type == "varchar(255)"


def test_primary_keys_follow_upstream() -> None:
    tables = ddl.tables()
    assert tables["person"].primary_key == ("person_id",)
    assert tables["note_nlp"].primary_key == ("note_nlp_id",)
    assert tables["concept"].primary_key == ("concept_id",)
    # Upstream defines no primary key for these tables; we stay faithful to that.
    for name in ("death", "cdm_source", "vocabulary", "concept_ancestor", "concept_relationship"):
        assert tables[name].primary_key == ()


def test_rendered_sql_is_fully_qualified() -> None:
    rendered = (
        ddl.render_tables()
        + ddl.render_primary_keys()
        + ddl.render_foreign_keys()
        + ddl.render_indices()
    )
    assert rendered
    for statement in rendered:
        assert ddl.PLACEHOLDER not in statement
        assert "CLUSTER" not in statement.upper()
    creates = ddl.render_tables()
    assert len(creates) == len(ddl.CLINICAL_TABLES) + len(ddl.VOCAB_TABLES)
    assert any(s.startswith("CREATE TABLE vocab.concept (") for s in creates)
    assert any(s.startswith("CREATE TABLE cdm.person (") for s in creates)


def test_foreign_keys_stay_between_clinical_tables() -> None:
    keys = ddl.foreign_keys()
    assert keys
    for key in keys:
        assert key.table in ddl.CLINICAL_TABLES
        assert key.ref_table in ddl.CLINICAL_TABLES
    assert ("measurement", "person_id", "person") in {
        (k.table, k.column, k.ref_table) for k in keys
    }
    assert ("note_nlp", "note_id", "note") in {(k.table, k.column, k.ref_table) for k in keys}
    for statement in ddl.render_foreign_keys():
        assert "REFERENCES vocab." not in statement


def test_indices_cover_person_concept_and_vocabulary_lookups() -> None:
    indices = " ".join(ddl.render_indices())
    assert "ON cdm.measurement  (person_id ASC)" in indices
    assert "ON cdm.measurement (measurement_concept_id ASC)" in indices
    assert "ON vocab.concept_ancestor (descendant_concept_id ASC)" in indices


def test_statement_about_unknown_table_is_rejected() -> None:
    with pytest.raises(ValueError, match="visit_detail"):
        ddl.qualify("SELECT 1 FROM @cdmDatabaseSchema.visit_detail")


def test_mapping_table_renders_only_when_asked() -> None:
    assert not any("source_to_concept_map" in s for s in ddl.render_tables())
    creates = ddl.render_tables(wanted=ddl.MAPPING_TABLES)
    assert creates == [
        s for s in creates if s.startswith("CREATE TABLE vocab.source_to_concept_map")
    ]
    assert len(creates) == 1
    indexes = ddl.render_indices(wanted=ddl.MAPPING_TABLES)
    assert len(indexes) == 4
    assert all("vocab.source_to_concept_map" in s for s in indexes)
