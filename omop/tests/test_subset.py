from pathlib import Path

from txlens_omop.athena import AthenaSource
from txlens_omop.mappings import LOCAL_CONCEPT_FILE, STCM_FILE, mappings_dir, read_mappings
from txlens_omop.subset import extract_subset

B = 2_100_000_000


def concept_ids(folder: Path, name: str = "CONCEPT.csv") -> set[int]:
    lines = (folder / name).read_text().splitlines()[1:]
    return {int(line.split("\t")[0]) for line in lines}


def test_subset_keeps_seeds_mappings_and_hierarchy(fake_zip: Path, tmp_path: Path) -> None:
    out = tmp_path / "subset"
    seeds = [("ICD10CM", "TEST-T86.11"), ("RxNorm", "TEST-RX-42316"), ("SNOMED", "NOPE")]
    report = extract_subset(AthenaSource(fake_zip), seeds, out)

    # seed 10 -> maps to 2 -> ancestor 1, descendants 3, 4; seed 30 -> descendants 31, 32;
    # 33 is a Brand Name of 30; 60 is a support vocabulary (Gender), kept whole.
    expected = {B + n for n in (10, 2, 1, 3, 4, 30, 31, 32, 33, 60)}
    assert concept_ids(out) == expected
    assert report.concepts == len(expected)
    assert report.seeds_found == 2
    assert report.seeds_missing == [("SNOMED", "NOPE")]


def test_subset_link_rows_only_reference_kept_concepts(fake_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "subset"
    extract_subset(AthenaSource(fake_dir), [("ICD10CM", "TEST-T86.11")], out)
    kept = concept_ids(out)
    for name in ("CONCEPT_RELATIONSHIP.csv", "CONCEPT_ANCESTOR.csv"):
        for line in (out / name).read_text().splitlines()[1:]:
            first, second = line.split("\t")[:2]
            assert int(first) in kept and int(second) in kept
    relationships = (out / "CONCEPT_RELATIONSHIP.csv").read_text()
    assert f"{B + 10}\t{B + 2}\tMaps to" in relationships


def test_subset_output_is_a_valid_athena_source(fake_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "subset"
    extract_subset(AthenaSource(fake_dir), [("LOINC", "TEST-LN-2160")], out)
    with AthenaSource.find(out).rows("vocabulary") as (header, lines):
        assert header[0] == "vocabulary_id"
        assert len(list(lines)) == 8


def test_subset_ships_our_reviewed_mapping_files(fake_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "subset"
    extract_subset(AthenaSource(fake_dir), [("LOINC", "TEST-LN-2160")], out)
    assert read_mappings(out / STCM_FILE) == read_mappings()
    assert (out / LOCAL_CONCEPT_FILE).read_text() == (
        mappings_dir() / LOCAL_CONCEPT_FILE
    ).read_text()
