"""Bronze keeps every raw row as-is, with exact lineage, and never ingests a file twice."""

from pathlib import Path

import pytest

pytest.importorskip("pyspark", reason="PySpark runs in the Spark container or CI")

from rawfiles import write_raw

from txlens_pipelines.bronze.labs import DIALECTS, check_header, split_lines
from txlens_pipelines.bronze.run import run_bronze
from txlens_pipelines.config import PipelineConfig
from txlens_pipelines.quality import ContractViolationError
from txlens_pipelines.runtime import LocalRuntime

pytestmark = pytest.mark.spark


@pytest.fixture
def config(runtime: LocalRuntime) -> PipelineConfig:
    write_raw(Path(runtime.raw_root))
    return PipelineConfig(runtime.raw_root, runtime.vocab_root)


def test_lab_rows_keep_raw_values_and_exact_row_numbers(
    runtime: LocalRuntime, config: PipelineConfig
) -> None:
    added = run_bronze(runtime, config)
    assert added == {"lab_rows": 16, "fhir_resources": 22, "note_files": 4, "pdf_index": 3}
    rows = {(r.tenant_id, r.source_row): r for r in runtime.read("bronze", "lab_rows").collect()}
    second = rows[("center_a", 2)]
    assert second.source_file == "labs/center_a_labs.csv"
    assert second.fields["TEST_NAME"] == "Creatinine, serum"  # quoted comma understood
    assert second.fields["RESULT"] == "  1.30 "  # whitespace kept as-is
    assert second.raw_line == 'A1,"Creatinine, serum",,  1.30 ,mg/dL,2024-08-10 07:22'
    assert rows[("center_a", 5)].fields["RESULT"] is None  # empty value
    assert rows[("center_b", 1)].fields["unit"] == "µmol/L"
    assert rows[("center_c", 2)].fields["ResultValue"] == "<0.05"
    assert len(second.file_sha256) == 64 and second.batch_id == config.batch_id


def test_rerun_adds_nothing_and_changed_file_adds_a_new_version(
    runtime: LocalRuntime, config: PipelineConfig
) -> None:
    run_bronze(runtime, config)
    assert set(run_bronze(runtime, config).values()) == {0}
    feed = Path(runtime.raw_root) / "labs" / "center_c_labs.csv"
    feed.write_bytes(
        feed.read_bytes() + b"730000063,EGFR,eGFR,60,mL/min/1.73m2,6/9/2022 7:31 AM\r\n"
    )
    assert run_bronze(runtime, config)["lab_rows"] == 7
    versions = runtime.read("bronze", "lab_rows").where("tenant_id = 'center_c'")
    assert versions.select("file_sha256").distinct().count() == 2
    assert versions.count() == 13  # the old version's rows are never changed or removed


def test_fhir_resources_are_one_row_per_entry(
    runtime: LocalRuntime, config: PipelineConfig
) -> None:
    run_bronze(runtime, config.only(["center_b"]))
    rows = runtime.read("bronze", "fhir_resources").orderBy("source_row").collect()
    assert [(r.source_row, r.resource_type, r.resource_id) for r in rows[:2]] == [
        (1, "Patient", "pb"),
        (2, "Procedure", "pb-tx"),
    ]
    assert len(rows) == 10
    assert rows[0].tenant_id == "center_b"
    assert rows[0].source_file == "fhir/center_b/center_b.json"
    assert '"value":"MRN-B-1"' in rows[0].resource_json  # same content, compact JSON


def test_notes_and_pdfs_are_indexed_per_file(runtime: LocalRuntime, config: PipelineConfig) -> None:
    run_bronze(runtime, config.only(["center_a"]))
    note = runtime.read("bronze", "note_files").first()
    assert note is not None and "MRN: A1" in note.text
    pdf = runtime.read("bronze", "pdf_index").first()
    assert pdf is not None
    assert (pdf.source_file, pdf.file_bytes) == ("pathology/center_a/BX-000001-01.pdf", 13)
    assert "content" not in runtime.read("bronze", "pdf_index").columns


def test_changed_lab_header_is_a_contract_violation(runtime: LocalRuntime) -> None:
    files = runtime.spark.createDataFrame(
        [("labs/x.csv", bytearray(b"MRN,NAME\r\nA1,x\r\n"))], "source_file string, content binary"
    )
    with pytest.raises(ContractViolationError, match="header"):
        check_header(split_lines(files), DIALECTS["a"], "x.csv")
