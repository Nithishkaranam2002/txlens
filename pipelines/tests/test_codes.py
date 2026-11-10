"""The pipeline's codes are all checked by `make vocab-check` (no Spark needed)."""

from txlens_datagen.writers.lab_feeds import DIALECTS
from txlens_omop.codes import all_codes
from txlens_omop.mappings import LOCAL_CONCEPT_FILE, mappings_dir
from txlens_pipelines.codes import BIOPSY_CODES, LAB_RULES


def checked() -> set[tuple[str, str]]:
    return {(c.vocabulary_id, c.code) for c in all_codes()}


def test_lab_and_unit_codes_are_in_the_vocab_check() -> None:
    local = (mappings_dir() / LOCAL_CONCEPT_FILE).read_text()
    for rule in LAB_RULES:
        if rule.vocabulary_id == "TxLens":
            assert f"\t{rule.concept_code}\t" in local
        else:
            assert (rule.vocabulary_id, rule.concept_code) in checked()
        if rule.ucum_code:
            assert ("UCUM", rule.ucum_code) in checked()


def test_biopsy_codes_are_in_the_vocab_check() -> None:
    assert set(BIOPSY_CODES.values()) <= checked()


def test_every_unit_the_feeds_write_has_a_conversion() -> None:
    units = {"creatinine": {"mg/dl", "umol/l"}, "egfr": {"ml/min/1.73m2"},
             "tacrolimus_trough": {"ng/ml", "ug/l"}, "dd_cfdna": {"%", "fraction"},
             "dsa_mfi": {"mfi"}}  # fmt: skip
    rules = {r.lab: set(r.factors) for r in LAB_RULES}
    assert rules == units
    assert {lab for d in DIALECTS.values() for lab in d.test_names} == set(rules)


def test_omop_column_copy_matches_the_pinned_ddl() -> None:
    from txlens_omop import ddl
    from txlens_pipelines.omop_schema import OMOP_TABLES, PRIMARY_KEYS

    for table, columns in OMOP_TABLES.items():
        spec = ddl.tables()[table]
        assert columns == tuple((c.name, c.data_type, c.nullable) for c in spec.columns)
        assert PRIMARY_KEYS[table] == spec.primary_key
