"""Build the OMOP gold tables from silver: stable ids, conformed columns, lineage, gaps."""

from dataclasses import dataclass, field
from datetime import date

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from txlens_pipelines.codes import CDM_V543, ENCODING_UTF8, NOTE_CLASS, NOTE_LANGUAGE
from txlens_pipelines.config import PipelineConfig
from txlens_pipelines.gold import tables as t
from txlens_pipelines.gold.concepts import concept_id
from txlens_pipelines.gold.conform import check_omop_contract, conform
from txlens_pipelines.gold.gaps import mapping_gaps
from txlens_pipelines.ids import assign_ids
from txlens_pipelines.omop_schema import PRIMARY_KEYS
from txlens_pipelines.quality import QUARANTINE
from txlens_pipelines.runtime import Runtime
from txlens_pipelines.vocab import Vocabulary, load_vocabulary, read_athena

# OMOP table -> its id column (death and cdm_source have none).
ID_COLUMNS = {table: keys[0] for table, keys in PRIMARY_KEYS.items() if keys}


@dataclass
class GoldWriter:
    """Assigns ids, conforms, checks and writes gold tables; collects lineage."""

    runtime: Runtime
    config: PipelineConfig
    counts: dict[str, int] = field(default_factory=dict)
    written: dict[str, DataFrame] = field(default_factory=dict)
    lineage: list[DataFrame] = field(default_factory=list)

    @property
    def tenants(self) -> list[str]:
        return [x.tenant_id for x in self.config.tenants]

    def with_ids(self, rows: DataFrame, table: str) -> DataFrame:
        """Rows plus their stable id column."""
        return assign_ids(self.runtime, rows, table, ID_COLUMNS[table], self.config)

    def write(self, table: str, rows: DataFrame) -> DataFrame:
        """Conform, check the OMOP contract, write; returns the stored table."""
        omop = conform(rows, table)
        check_omop_contract(omop, table)
        self.runtime.replace_tenants(omop, "gold", table, self.tenants)
        self.counts[table] = omop.count()
        if "bronze_source_file" in rows.columns:
            id_column = ID_COLUMNS.get(table, "person_id")
            self.lineage.append(
                rows.select(
                    "tenant_id",
                    F.lit(table).alias("omop_table"),
                    F.col(id_column).cast("int").alias("omop_id"),
                    "bronze_source_file",
                    "bronze_source_row",
                )
            )
        stored = self.runtime.read_tenants("gold", table, self.tenants)
        self.written[table] = stored
        return stored

    def write_extra(self, table: str, df: DataFrame) -> None:
        """A non-OMOP gold table (identity, lineage, mapping gaps)."""
        self.runtime.replace_tenants(df, "gold", table, self.tenants)
        self.counts[table] = df.count()


def vocabulary_version(runtime: Runtime) -> str:
    """The Athena release of the vocabulary subset."""
    row = read_athena(runtime, "VOCABULARY.csv").where("vocabulary_id = 'None'").first()
    return str(row.vocabulary_version) if row else "unknown"


def cdm_source_rows(runtime: Runtime, config: PipelineConfig) -> DataFrame:
    """One cdm_source row per tenant."""
    today = date.today()
    version = vocabulary_version(runtime)
    rows = [
        (x.tenant_id, x.name, x.tenant_id, "TxLens (synthetic data)", today, today, "5.4",
         CDM_V543, version)
        for x in config.tenants
    ]  # fmt: skip
    return runtime.spark.createDataFrame(
        rows,
        "tenant_id string, cdm_source_name string, cdm_source_abbreviation string, "
        "cdm_holder string, source_release_date date, cdm_release_date date, "
        "cdm_version string, cdm_version_concept_id int, vocabulary_version string",
    )


def event_dates(df: DataFrame, column: str) -> DataFrame:
    """(tenant, person, date) of every event in a gold table."""
    return df.select("tenant_id", "person_id", F.col(column).alias("event_date"))


def run_gold(runtime: Runtime, config: PipelineConfig) -> dict[str, int]:
    """Build every gold table for the configured tenants; returns row counts."""
    vocab: Vocabulary = load_vocabulary(runtime)
    w = GoldWriter(runtime, config)

    def silver(name: str) -> DataFrame:
        return runtime.read_tenants("silver", name, w.tenants)

    persons = w.with_ids(t.person_rows(silver("patients")), "person")
    w.write("person", persons)
    w.write_extra("patient_identity", t.identity_rows(silver("patients"), persons))
    visits = w.with_ids(t.visit_rows(silver("encounters"), persons), "visit_occurrence")
    w.write("visit_occurrence", visits)
    procedures = silver("procedures")
    builders = {
        "drug_exposure": t.drug_rows(silver("medications"), persons, vocab),
        "measurement": t.measurement_rows(silver("labs"), persons, visits, vocab),
        "condition_occurrence": t.condition_rows(
            silver("conditions"), persons, procedures.where("kind = 'transplant'"), vocab
        ),
        "procedure_occurrence": t.procedure_rows(procedures, persons, visits, vocab),
        "note": t.note_rows(
            silver("notes"),
            persons,
            visits,
            concept_id(vocab, *NOTE_CLASS),
            concept_id(vocab, *NOTE_LANGUAGE),
            ENCODING_UTF8,
        ),
    }
    for table, rows in builders.items():
        w.write(table, w.with_ids(rows, table))
    w.write("death", t.death_rows(persons))
    events = [
        event_dates(w.written["visit_occurrence"], "visit_start_date"),
        event_dates(w.written["drug_exposure"], "drug_exposure_start_date"),
        event_dates(w.written["measurement"], "measurement_date"),
        event_dates(w.written["procedure_occurrence"], "procedure_date"),
        event_dates(w.written["condition_occurrence"], "condition_start_date"),
    ]
    periods = t.observation_period_rows(persons, events)
    w.write("observation_period", w.with_ids(periods, "observation_period"))
    w.write("cdm_source", cdm_source_rows(runtime, config))

    lineage = w.lineage[0]
    for frame in w.lineage[1:]:
        lineage = lineage.unionByName(frame)
    w.write_extra("lineage", lineage)
    quarantine = runtime.read_tenants(*QUARANTINE, w.tenants)
    w.write_extra("mapping_gaps", mapping_gaps(w.written, quarantine))
    return w.counts
