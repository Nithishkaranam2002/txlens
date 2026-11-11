"""Build every silver table for the configured tenants.

For each table: transform, then check its contract (quarantine rate, pandera schema, primary
key), and only then write it. Patients and encounters are written first and read back, because
the other entities need them to find their patient and visit.
"""

from dataclasses import dataclass, field

from pyspark.sql import DataFrame

from txlens_pipelines.config import PipelineConfig
from txlens_pipelines.quality import (
    QUARANTINE,
    SchemaModel,
    check_primary_key,
    check_quarantine_rate,
    to_quarantine,
    validate_schema,
)
from txlens_pipelines.runtime import Runtime
from txlens_pipelines.silver import conditions, encounters, labs, medications, notes, patients
from txlens_pipelines.silver import schemas as s
from txlens_pipelines.vocab import load_vocabulary


@dataclass
class SilverWriter:
    """Checks and writes silver tables, and collects quarantined rows."""

    runtime: Runtime
    config: PipelineConfig
    counts: dict[str, int] = field(default_factory=dict)
    quarantined: list[DataFrame] = field(default_factory=list)

    @property
    def tenants(self) -> list[str]:
        return [t.tenant_id for t in self.config.tenants]

    def write(
        self,
        table: str,
        good: DataFrame,
        bad: DataFrame | None,
        schema: SchemaModel,
        key: list[str],
    ) -> DataFrame:
        """Check the contract, write the table, and return it as stored."""
        good_rows = good.count()
        if bad is not None:
            bad_rows = bad.count()
            check_quarantine_rate(table, good_rows, bad_rows, self.config.max_quarantine_rate)
            self.quarantined.append(to_quarantine(bad, table, self.config.batch_id))
            self.counts[f"{table}_quarantined"] = bad_rows
        validate_schema(good, schema, f"silver.{table}")
        check_primary_key(good, f"silver.{table}", key)
        self.runtime.replace_tenants(good, "silver", table, self.tenants)
        self.counts[table] = good_rows
        return self.runtime.read_tenants("silver", table, self.tenants)

    def write_quarantine(self) -> None:
        """All quarantined rows of this run, replacing these tenants' earlier ones."""
        combined = self.quarantined[0]
        for frame in self.quarantined[1:]:
            combined = combined.unionByName(frame)
        self.runtime.replace_tenants(combined, *QUARANTINE, self.tenants)


def run_silver(runtime: Runtime, config: PipelineConfig) -> dict[str, int]:
    """Clean and standardize bronze into the silver tables; returns row counts."""
    vocab = load_vocabulary(runtime)
    w = SilverWriter(runtime, config)
    fhir = runtime.read_tenants("bronze", "fhir_resources", w.tenants)

    pat = w.write("patients", *patients.build(fhir), s.Patients, ["tenant_id", "patient_source_id"])
    check_primary_key(pat, "silver.patients", ["tenant_id", "mrn"])
    enc = w.write(
        encounters.ENCOUNTERS,
        *encounters.build_encounters(fhir, pat),
        s.Encounters,
        ["tenant_id", "encounter_source_id"],
    )
    transplants, bad_transplants = encounters.build_transplants(fhir, pat)
    w.write(
        encounters.PROCEDURES,
        transplants.unionByName(encounters.build_biopsies(enc, transplants)),
        bad_transplants,
        s.Procedures,
        ["tenant_id", "procedure_source_id"],
    )
    w.write(
        conditions.TABLE,
        *conditions.build(fhir, pat, vocab),
        s.Conditions,
        ["tenant_id", "condition_source_id"],
    )
    w.write(
        medications.TABLE,
        *medications.build(fhir, pat, vocab),
        s.Medications,
        ["tenant_id", "order_source_id"],
    )
    lab_rows = runtime.read_tenants("bronze", "lab_rows", w.tenants)
    clean_labs, bad_labs, duplicates = labs.build(lab_rows, pat, vocab)
    lab_key = ["tenant_id", "bronze_source_file", "bronze_source_row"]
    w.write(labs.TABLE, clean_labs, bad_labs, s.Labs, lab_key)
    w.write(labs.DUPLICATES, duplicates, None, s.Duplicates, lab_key)
    note_files = runtime.read_tenants("bronze", "note_files", w.tenants)
    w.write(
        notes.TABLE, *notes.build(note_files, pat, enc), s.Notes, ["tenant_id", "note_source_id"]
    )
    w.write_quarantine()
    return w.counts
