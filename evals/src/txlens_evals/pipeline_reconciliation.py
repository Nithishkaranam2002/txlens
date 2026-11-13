"""Reconcile the pipeline output with the generator's ground truth; write a data-quality report.

`make pipeline-report` (Spark container) writes evals/reports/data_quality.md with:
counts per layer, quarantine reasons, mapping coverage, mapping gaps, and reconciliation checks
(patients, deaths, labs row by row in standard units, medications, maintenance
immunosuppression coverage, duplicates removed, bad rows quarantined, Postgres vs gold).
Every number in the README's data-quality section comes from it.

Known, explained differences are reported as such, never hidden: the generator sometimes
writes two genuinely identical lab rows (same patient, test, time and value); an exact-duplicate
filter cannot tell them from a re-sent row, so one is removed. Such pairs are counted apart.
"""

import argparse
import csv
import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from pyspark.sql import Column, DataFrame, SparkSession
from pyspark.sql import functions as F

# Read tolerance per lab in standard units: center B writes creatinine as whole umol/L, so
# converting back to mg/dL is only accurate to 0.5 / 88.4 mg/dL.
TOLERANCE = {"creatinine": 0.006, "dd_cfdna": 1e-6}
DEFAULT_TOLERANCE = 0.05
BAD_ROW_ISSUES = ("bad_date", "missing_value")
# Maintenance immunosuppression per the generator's protocol: every kidney and heart patient
# stays on tacrolimus + mycophenolate + prednisone (RxNorm ingredient codes).
MAINTENANCE = {"tacrolimus": "42316", "mycophenolate mofetil": "68149", "prednisone": "8640"}
PROTOCOL_NOTE = (
    "Checked against the generator's protocol (tacrolimus + mycophenolate + prednisone for "
    "both organs). Real-world protocols vary, e.g. steroid-free or steroid-withdrawal regimens, "
    "belatacept or mTOR inhibitors, so this is a synthetic-data consistency check, not a "
    "clinical rule."
)
COVERAGE = (
    ("measurement", "measurement_concept_id"),
    ("measurement", "unit_concept_id"),
    ("drug_exposure", "drug_concept_id"),
    ("drug_exposure", "route_concept_id"),
    ("condition_occurrence", "condition_concept_id"),
    ("procedure_occurrence", "procedure_concept_id"),
)
LAYER_TABLES = {
    "bronze": ("lab_rows", "fhir_resources", "note_files", "pdf_index"),
    "silver": ("patients", "encounters", "procedures", "conditions", "medications", "labs",
               "lab_duplicates", "notes", "quarantine"),
    "gold": ("person", "observation_period", "visit_occurrence", "condition_occurrence",
             "observation", "drug_exposure", "measurement", "procedure_occurrence", "death", "note",
             "cdm_source", "patient_identity", "note_phi_span", "lineage", "mapping_gaps"),
}  # fmt: skip


CONDITION_METHODS = ("code", "curated", "exact", "normalized", "fuzzy", "unmapped")
REVIEW_CSV = "evals/reports/condition_mapping_review.csv"


@dataclass(frozen=True)
class Check:
    """One reconciliation check."""

    name: str
    expected: int
    actual: int
    passed: bool
    note: str = ""


class Lake:
    """Reads Delta tables wherever the pipeline wrote them (path or Unity Catalog)."""

    def __init__(self, spark: SparkSession, lake_root: str | None, catalog: str | None) -> None:
        self.spark, self.lake_root, self.catalog = spark, lake_root, catalog

    def read(self, layer: str, table: str) -> DataFrame:
        if self.catalog:
            return self.spark.read.table(f"{self.catalog}.txlens_{layer}.{table}")
        return self.spark.read.format("delta").load(f"{self.lake_root}/{layer}/{table}")


def read_truth(spark: SparkSession, folder: str, name: str) -> DataFrame:
    """One ground-truth CSV, every column a string."""
    return spark.read.option("header", True).csv(f"{folder}/{name}.csv")


def file_name(column: str) -> Column:
    """'labs/center_a_labs.csv' -> 'center_a_labs.csv'."""
    return F.element_at(F.split(F.col(column), "/"), -1)


def count(df: DataFrame) -> int:
    return int(df.count())


def persons_by_mrn(lake: Lake) -> DataFrame:
    """gold.person with each person's MRN and exact birth date from gold.patient_identity.

    OMOP holds no identifiers, so the identity table is the only link to the source MRN.
    A person without an identity row keeps a NULL MRN and so shows up as missing and extra.
    """
    identity = lake.read("gold", "patient_identity").select(
        "tenant_id", "person_id", "mrn", "birth_date"
    )
    return lake.read("gold", "person").join(identity, ["tenant_id", "person_id"], "left")


def check_patients(lake: Lake, truth: DataFrame) -> list[Check]:
    """Same MRNs per tenant; same sex, birth year (OMOP) and birth date (identity table)."""
    person = persons_by_mrn(lake).select(
        "tenant_id", "mrn", "gender_concept_id", "year_of_birth",
        F.col("birth_date").alias("identity_birth_date"),
    )  # fmt: skip
    expected = truth.select("tenant_id", "mrn", F.to_date("birth_date").alias("birth"), "sex")
    joined = expected.join(person, ["tenant_id", "mrn"], "full")
    missing = count(joined.where(F.col("gender_concept_id").isNull()))
    extra = count(joined.where(F.col("sex").isNull()))
    gender = F.when(F.col("sex") == "F", 8532).otherwise(8507)
    wrong = count(
        joined.where(
            (F.col("gender_concept_id") != gender)
            | (F.col("year_of_birth") != F.year("birth"))
            | (F.col("identity_birth_date") != F.col("birth"))
        )
    )
    total = count(truth)
    return [
        Check("patients: one person per ground-truth patient (MRN, per tenant)", total,
              count(person), missing == 0 and extra == 0, f"missing {missing}, extra {extra}"),
        Check("patients: sex, birth year (OMOP) and birth date (identity)", total,
              total - wrong, wrong == 0, f"{wrong} differ"),
    ]  # fmt: skip


def check_deaths(lake: Lake, patients: DataFrame, outcomes: DataFrame) -> Check:
    """Every death with the right date, and no others."""
    truth = (
        outcomes.where(F.col("death_date").isNotNull())
        .join(patients.select("patient_id", "tenant_id", "mrn"), "patient_id")
        .select("tenant_id", "mrn", F.to_date("death_date").alias("death_date"))
    )
    person = persons_by_mrn(lake).select("tenant_id", "person_id", "mrn")
    gold = (
        lake.read("gold", "death")
        .join(person, ["tenant_id", "person_id"])
        .select("tenant_id", "mrn", "death_date")
    )
    differ = count(truth.exceptAll(gold)) + count(gold.exceptAll(truth))
    return Check("deaths: same people and dates", count(truth), count(gold), differ == 0,
                 f"{differ} differ")  # fmt: skip


def lab_issues(issues: DataFrame) -> DataFrame:
    """(raw_file, raw_row) -> set of injected issues."""
    return issues.groupBy("raw_file", F.col("raw_row").cast("int").alias("raw_row")).agg(
        F.collect_set("issue").alias("issues")
    )


def identical_pairs(lake: Lake, labs_truth: DataFrame) -> DataFrame:
    """Removed 'duplicates' that are genuine results identical to another genuine result."""
    removed = lake.read("silver", "lab_duplicates").select(
        file_name("bronze_source_file").alias("raw_file"),
        F.col("bronze_source_row").alias("raw_row"),
    )
    genuine = labs_truth.select("raw_file", F.col("raw_row").cast("int").alias("raw_row"))
    return removed.join(genuine, ["raw_file", "raw_row"])


def check_labs(lake: Lake, labs_truth: DataFrame, issues: DataFrame) -> list[Check]:
    """Every result without a bad date or value is in silver, in standard units."""
    flagged = lab_issues(issues)
    truth = (
        labs_truth.withColumn("raw_row", F.col("raw_row").cast("int"))
        .join(flagged, ["raw_file", "raw_row"], "left")
        .withColumn("bad", F.arrays_overlap(
            F.coalesce("issues", F.array().cast("array<string>")),
            F.array(*[F.lit(i) for i in BAD_ROW_ISSUES])))
    )  # fmt: skip
    expected = truth.where(~F.col("bad"))
    silver = lake.read("silver", "labs").select(
        file_name("bronze_source_file").alias("raw_file"),
        F.col("bronze_source_row").alias("raw_row"),
        F.col("patient_source_id").alias("s_patient"),
        F.col("lab").alias("s_lab"),
        F.col("collected_at").alias("s_collected_at"),
        "value_as_number",
        "value_operator",
    )
    joined = expected.join(silver, ["raw_file", "raw_row"], "left")
    pairs = identical_pairs(lake, labs_truth).withColumn("pair", F.lit(True))
    joined = joined.join(pairs, ["raw_file", "raw_row"], "left")
    missing = joined.where(F.col("s_lab").isNull())
    explained = count(missing.where(F.col("pair")))
    unexplained = count(missing) - explained
    found = joined.where(F.col("s_lab").isNotNull())
    tol = F.coalesce(
        F.create_map(*[F.lit(x) for kv in TOLERANCE.items() for x in kv])[F.col("lab")],
        F.lit(DEFAULT_TOLERANCE),
    )
    truth_value = F.col("value").cast("double")
    value_ok = (
        F.when(F.col("value_operator") == "<", truth_value < F.col("value_as_number"))
        .when(F.col("value_operator") == ">", truth_value > F.col("value_as_number"))
        .otherwise(F.abs(truth_value - F.col("value_as_number")) <= tol + 1e-9)
    )
    wrong = found.where(
        (F.col("s_patient") != F.col("patient_id"))
        | (F.col("s_lab") != F.col("lab"))
        | (F.col("s_collected_at") != F.to_timestamp("collected_at"))
        | ~value_ok
    )
    censored = count(found.where(F.col("value_operator").isNotNull()))
    n_expected, n_found, n_wrong = count(expected), count(found), count(wrong)
    gold = count(lake.read("gold", "measurement"))
    return [
        Check("labs: every clean ground-truth result reaches silver", n_expected, n_found,
              unexplained == 0,
              f"{explained} not separate: genuinely identical to another result"
              if explained else ""),
        Check("labs: patient, test, time and value (standard units) match", n_found,
              n_found - n_wrong, n_wrong == 0,
              f"{censored:,} censored results checked against their bound"),
        Check("labs: gold.measurement has one row per silver lab", count(silver), gold,
              gold == count(silver)),
    ]  # fmt: skip


def check_medications(lake: Lake, meds: DataFrame, vocab_root: str) -> list[Check]:
    """One drug_exposure per order; ingredient, dose and start date match."""
    spark = meds.sparkSession
    concepts = (
        spark.read.option("sep", "\t").option("header", True).option("quote", "\u0000")
        .csv(f"{vocab_root}/CONCEPT.csv")
        .where("vocabulary_id = 'RxNorm'")
        .select(F.col("concept_code").alias("rxnorm"),
                F.col("concept_id").cast("int").alias("truth_concept"))
    )  # fmt: skip
    silver = lake.read("silver", "medications").select(
        F.col("order_source_id").alias("order_id"),
        "drug_concept_id",
        F.col("dose_value").alias("s_dose"),
        F.col("start_date").alias("s_start"),
    )
    joined = meds.join(concepts, "rxnorm", "left").join(silver, "order_id", "left")
    total = count(meds)
    missing = count(joined.where(F.col("drug_concept_id").isNull()))
    right_drug = count(joined.where(F.col("drug_concept_id") == F.col("truth_concept")))
    dose_ok = count(
        joined.where(
            (F.abs(F.col("s_dose") - F.col("dose_value").cast("double")) < 1e-9)
            & (F.col("s_start") == F.to_date("start_date"))
        )
    )
    gold = count(lake.read("gold", "drug_exposure"))
    return [
        Check("medications: one drug_exposure per order", total, gold,
              gold == total and missing == 0, f"{missing} orders missing"),
        Check("medications: normalized to the right RxNorm ingredient", total, right_drug,
              right_drug == total, f"{right_drug / total:.1%} correct" if total else ""),
        Check("medications: dose and start date", total, dose_ok, dose_ok == total),
    ]  # fmt: skip


def uncovered_days(spans: Sequence[tuple[date, date]], start: date, end: date) -> int:
    """Days in [start, end] (inclusive) that no span [from, to] covers."""
    covered: set[date] = set()
    for first, last in spans:
        day = max(first, start)
        while day <= min(last, end):
            covered.add(day)
            day += timedelta(days=1)
    return (end - start).days + 1 - len(covered)


@dataclass(frozen=True)
class MaintenanceGap:
    """An active patient without continuous coverage by one maintenance drug."""

    tenant_id: str
    mrn: str
    drug: str
    uncovered_days: int


def maintenance_gaps(
    lake: Lake, patients: DataFrame, outcomes: DataFrame, vocab_root: str, extracted: date
) -> tuple[int, list[MaintenanceGap]]:
    """(active patients, their gaps): every patient alive with a working graft at the extract
    must have each maintenance drug on every day from transplant to the extract date."""
    spark = patients.sparkSession
    active = (
        patients.join(outcomes, "patient_id")
        .where(F.col("death_date").isNull() & F.col("graft_loss_date").isNull())
        .select("tenant_id", "mrn", F.to_date("transplant_date").alias("transplant_date"))
    )
    codes = {code: drug for drug, code in MAINTENANCE.items()}
    concepts = (
        spark.read.option("sep", "\t").option("header", True).option("quote", "\u0000")
        .csv(f"{vocab_root}/CONCEPT.csv")
        .where(F.col("vocabulary_id") == "RxNorm")
        .where(F.col("concept_code").isin(*codes))
        .select(F.col("concept_id").cast("int").alias("drug_concept_id"),
                F.col("concept_code").alias("rxnorm"))
    )  # fmt: skip
    person = persons_by_mrn(lake).select("tenant_id", "person_id", "mrn")
    exposures = (
        lake.read("gold", "drug_exposure").join(concepts, "drug_concept_id")
        .join(person, ["tenant_id", "person_id"])
        .select("tenant_id", "mrn", "rxnorm", "drug_exposure_start_date", "drug_exposure_end_date")
    )  # fmt: skip
    spans: dict[tuple[str, str, str], list[tuple[date, date]]] = defaultdict(list)
    for r in exposures.collect():
        spans[(r.tenant_id, r.mrn, r.rxnorm)].append(
            (r.drug_exposure_start_date, r.drug_exposure_end_date)
        )
    people = active.collect()
    gaps = [
        MaintenanceGap(p.tenant_id, p.mrn, drug, missing)
        for p in people
        for code, drug in codes.items()
        if (missing := uncovered_days(spans[(p.tenant_id, p.mrn, code)], p.transplant_date,
                                      extracted))
    ]  # fmt: skip
    return len(people), gaps


def check_maintenance(n_active: int, gaps: Sequence[MaintenanceGap], extracted: date) -> Check:
    """Every active patient covered by all maintenance drugs up to the extract date."""
    without = len({(g.tenant_id, g.mrn) for g in gaps})
    return Check(
        "medications: continuous maintenance immunosuppression per the generator's protocol "
        f"(active patients, transplant to extract {extracted.isoformat()})",
        n_active, n_active - without, without == 0,
        f"{without} patients with gaps ({len(gaps)} patient-drug gaps); see section below",
    )  # fmt: skip


def read_extract_date(raw_root: str) -> date:
    """The source extract date the generator wrote next to the raw feeds."""
    info = json.loads(Path(raw_root, "extract.json").read_text(encoding="utf-8"))
    return date.fromisoformat(info["extract_date"])


def check_duplicates(lake: Lake, issues: DataFrame, labs_truth: DataFrame) -> Check:
    """Removed lines = injected duplicates, plus explained identical genuine pairs."""
    injected = issues.where("issue = 'duplicate'").select(
        "raw_file", F.col("raw_row").cast("int").alias("raw_row")
    )
    removed = lake.read("silver", "lab_duplicates").select(
        file_name("bronze_source_file").alias("raw_file"),
        F.col("bronze_source_row").alias("raw_row"),
    )
    missed = count(injected.exceptAll(removed))
    extra = count(removed.exceptAll(injected))
    pairs = count(identical_pairs(lake, labs_truth))
    return Check("duplicates: every injected duplicate removed, nothing else", count(injected),
                 count(removed), missed == 0 and extra == pairs,
                 f"missed {missed}; {extra} extra, of which {pairs} are identical genuine "
                 "results")  # fmt: skip


def check_quarantine(lake: Lake, issues: DataFrame) -> Check:
    """Quarantined lab rows = rows with an injected bad date or missing value."""
    bad = (
        issues.where(F.col("issue").isin(*BAD_ROW_ISSUES))
        .select("raw_file", F.col("raw_row").cast("int").alias("raw_row"))
        .distinct()
    )
    duplicates = lake.read("silver", "lab_duplicates").select(
        file_name("bronze_source_file").alias("raw_file"),
        F.col("bronze_source_row").alias("raw_row"),
    )
    expected = bad.exceptAll(bad.intersect(duplicates))  # a removed copy is not quarantined
    got = (
        lake.read("silver", "quarantine")
        .where("entity = 'labs'")
        .select(file_name("source_file").alias("raw_file"), F.col("source_row").alias("raw_row"))
    )
    missed, extra = count(expected.exceptAll(got)), count(got.exceptAll(expected))
    return Check("quarantine: exactly the rows with an injected bad date or missing value",
                 count(expected), count(got), missed == 0 and extra == 0,
                 f"missed {missed}, extra {extra}")  # fmt: skip


def layer_counts(lake: Lake) -> list[tuple[str, str, int]]:
    """(layer, table, rows)."""
    return [
        (layer, table, count(lake.read(layer, table)))
        for layer, tables in LAYER_TABLES.items()
        for table in tables
    ]


def coverage(lake: Lake) -> list[tuple[str, str, str, int, int]]:
    """(table, field, tenant, mapped rows, total rows)."""
    rows = []
    for table, field in COVERAGE:
        grouped = (
            lake.read("gold", table)
            .groupBy("tenant_id")
            .agg(
                F.sum((F.col(field) != 0).cast("int")).alias("mapped"), F.count("*").alias("total")
            )
        )
        for r in grouped.orderBy("tenant_id").collect():
            rows.append((table, field, r.tenant_id, int(r.mapped), int(r.total)))
    return rows


# A note leaks if it still holds its own patient's MRN, a name (whole word, any case) or the
# birth date in any style the notes use. Same rule for gold (Spark) and Postgres (SQL).
LEAK_SQL = """
    position(lower(i.mrn) in lower(n.note_text)) > 0
    OR (length(trim(i.given_name)) > 1
        AND n.note_text ~* ('\\m' || trim(i.given_name) || '\\M'))
    OR (length(trim(i.family_name)) > 1
        AND n.note_text ~* ('\\m' || trim(i.family_name) || '\\M'))
    OR position(to_char(i.birth_date, 'YYYY-MM-DD') in n.note_text) > 0
    OR position(to_char(i.birth_date, 'MM/DD/YYYY') in n.note_text) > 0
    OR position(to_char(i.birth_date, 'FMMM/FMDD/YY') in n.note_text) > 0
"""


def leaked(text: str, mrn: str, given: str, family: str, born: str) -> Column:
    """True when a note text column still holds its patient's identifiers (see LEAK_SQL)."""

    def word(name: str) -> str:
        pattern = f"concat('(?i)\\\\b', trim({name}), '\\\\b')"
        return f"(length(trim({name})) > 1 AND coalesce({text} rlike {pattern}, false))"

    short = f"concat_ws('/', month({born}), dayofmonth({born}), date_format({born}, 'yy'))"
    return F.expr(
        f"instr(lower({text}), lower({mrn})) > 0 OR {word(given)} OR {word(family)} "
        f"OR instr({text}, date_format({born}, 'yyyy-MM-dd')) > 0 "
        f"OR instr({text}, date_format({born}, 'MM/dd/yyyy')) > 0 "
        f"OR instr({text}, {short}) > 0"
    )


def check_note_deid(lake: Lake) -> Check:
    """No gold note holds an identifier of its own patient."""
    notes = lake.read("gold", "note").join(lake.read("gold", "patient_identity"), "person_id")
    total = count(notes)
    leaks = count(
        notes.where(leaked("note_text", "mrn", "given_name", "family_name", "birth_date"))
    )  # fmt: skip
    return Check("notes: no identifier of the patient left in cdm.note text (gold)", 0, leaks,
                 leaks == 0, f"{total:,} notes checked (MRN, names, birth date)")  # fmt: skip


def postgres_note_leaks(tenants: Sequence[str]) -> int | None:
    """Loaded notes that still hold an identifier from app.patient_identity (as the app role)."""
    try:
        import psycopg

        from txlens_pipelines.load.postgres import database_url

        leaks = 0
        with psycopg.connect(database_url(), autocommit=True, connect_timeout=3) as conn:
            for tenant in tenants:
                with conn.transaction():
                    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))
                    row = conn.execute(
                        "SELECT count(*) FROM cdm.note n JOIN app.patient_identity i "
                        f"USING (person_id) WHERE {LEAK_SQL}"
                    ).fetchone()
                    leaks += int(str(row[0])) if row else 0
        return leaks
    except (ImportError, SystemExit, Exception) as error:  # no psycopg, no URL, no server
        print(f"Postgres note check skipped: {error}")
        return None


def condition_methods(lake: Lake) -> list[tuple[str, dict[str, int], dict[str, int]]]:
    """Per tenant: FHIR condition rows by mapping method, and by OMOP domain (mapped rows)."""
    conditions = lake.read("silver", "conditions")
    methods = conditions.groupBy("tenant_id", "mapping_method").count().collect()
    domains = (
        conditions.where(F.col("condition_concept_id") != 0)
        .groupBy("tenant_id", "domain_id").count().collect()
    )  # fmt: skip
    tenants = sorted({r.tenant_id for r in methods})
    return [
        (tenant,
         {r.mapping_method: int(r["count"]) for r in methods if r.tenant_id == tenant},
         {r.domain_id: int(r["count"]) for r in domains if r.tenant_id == tenant})
        for tenant in tenants
    ]  # fmt: skip


def render_conditions(
    by_tenant: Sequence[tuple[str, dict[str, int], dict[str, int]]],
) -> list[str]:
    """Coverage of FHIR conditions with codes only (before Step 2.2) and with text matching,
    rows per mapping method, and where mapped rows went (condition_occurrence/observation)."""
    rows, method_rows = [], []
    totals = {m: 0 for m in CONDITION_METHODS} | {"Condition": 0, "Observation": 0}
    for tenant, methods, domains in [*by_tenant, ("**all**", {}, {})]:
        if tenant == "**all**":
            methods = {m: totals[m] for m in CONDITION_METHODS}
            domains = {d: totals[d] for d in ("Condition", "Observation")}
        else:
            for key, value in (methods | domains).items():
                totals[key] += value
        total = sum(methods.values())
        coded = methods.get("code", 0)
        mapped = total - methods.get("unmapped", 0)
        rows.append((tenant, total, coded, f"{coded / total:.1%}" if total else "-",
                     mapped, f"{mapped / total:.1%}" if total else "-",
                     domains.get("Condition", 0), domains.get("Observation", 0)))  # fmt: skip
        method_rows.append((tenant, *(methods.get(m, 0) for m in CONDITION_METHODS)))
    return [
        "## Free-text conditions (Step 2.2)",
        "",
        "Every FHIR Condition, whichever OMOP table its concept's domain sends it to. "
        "\"Codes only\" is what the pipeline mapped before Step 2.2; text matching adds the "
        "curated map (TXLENS_CONDITION rows: curated by AI assistant, pending clinical "
        "review), exact name or synonym, normalized and fuzzy matches (fuzzy accepted at "
        ">= 0.90 and only when type, acuity, side, grade, stage and numbers match exactly). "
        f"Unmapped texts and their best suggestion are in `{REVIEW_CSV}`.",
        "",
        *table(["Tenant", "Conditions", "Codes only", "Coverage before", "Mapped now",
                "Coverage now", "-> condition_occurrence", "-> observation"], rows),
        "",
        "Rows by mapping method:",
        "",
        *table(["Tenant", *CONDITION_METHODS], method_rows),
        "",
    ]  # fmt: skip


def write_review_csv(lake: Lake, vocab_root: str, path: str) -> None:
    """Unmapped condition texts with the matcher's suggestion, for a reviewer."""
    spark = lake.spark
    names = (
        spark.read.option("sep", "\t").option("header", True).option("quote", "\u0000")
        .csv(f"{vocab_root}/CONCEPT.csv")
        .select(F.col("concept_id").cast("int").alias("suggested_concept_id"),
                F.col("concept_name").alias("suggested_name"))
    )  # fmt: skip
    gaps = (
        lake.read("gold", "mapping_gaps")
        .where(F.col("omop_table") == "condition_occurrence")
        .join(names, "suggested_concept_id", "left")
        .orderBy(F.desc("rows"), "tenant_id", "source_value")
        .collect()
    )
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["tenant_id", "source_value", "rows", "review_reason",
                         "suggested_concept_id", "suggested_name", "suggestion_score"])  # fmt: skip
        writer.writerows(
            [g.tenant_id, g.source_value, g.rows, g.review_reason,
             g.suggested_concept_id, g.suggested_name, g.suggestion_score]
            for g in gaps
        )  # fmt: skip


def postgres_counts(tenants: Sequence[str]) -> dict[tuple[str, str], int] | None:
    """(tenant, table) -> pipeline rows in Postgres as the app role; None if Postgres is
    unavailable."""
    try:
        import psycopg

        from txlens_pipelines.load.postgres import (
            FIRST_APP_ROW_ID,
            LOAD_ORDER,
            database_url,
            schema_of,
        )
        from txlens_pipelines.omop_schema import PRIMARY_KEYS

        result = {}
        with psycopg.connect(database_url(), autocommit=True, connect_timeout=3) as conn:
            for tenant in tenants:
                with conn.transaction():
                    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))
                    # mapping_gap is a view of gold.mapping_gaps for review, not a gold table.
                    for table in (t for t in LOAD_ORDER if t != "mapping_gap"):
                        name = f"{schema_of(table)}.{table}"
                        key = PRIMARY_KEYS.get(table, ())
                        # Only the pipeline's rows: extraction results are copied back later.
                        where = f" WHERE {key[0]} < {FIRST_APP_ROW_ID}" if key else ""
                        row = conn.execute(f"SELECT count(*) FROM {name}{where}").fetchone()
                        result[(tenant, table)] = int(str(row[0])) if row else 0
        return result
    except (ImportError, SystemExit, Exception) as error:  # no psycopg, no URL, no server
        print(f"Postgres check skipped: {error}")
        return None


def check_postgres(lake: Lake, pg: dict[tuple[str, str], int]) -> Check:
    """Postgres row counts equal gold row counts, per tenant and table."""
    gold: dict[tuple[str, str], int] = {}
    for table in sorted({t for _, t in pg}):
        for r in lake.read("gold", table).groupBy("tenant_id").count().collect():
            gold[(r.tenant_id, table)] = int(r["count"])
    differ = [k for k in pg if pg[k] != gold.get(k, 0)]
    note = f"{len(pg)} (tenant, table) pairs compared" + (f"; differ: {differ}" if differ else "")
    return Check("postgres: cdm + identity row counts equal gold, per tenant and table",
                 sum(gold.get(k, 0) for k in pg), sum(pg.values()), not differ, note)  # fmt: skip


def table(header: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    """A markdown table."""

    def cell(value: object) -> str:
        return f"{value:,}" if isinstance(value, int) else str(value)

    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return lines


def render(
    checks: list[Check],
    counts: list[tuple[str, str, int]],
    reasons: list[tuple[str, str, str, int]],
    cover: list[tuple[str, str, str, int, int]],
    gaps: list[tuple[str, str, str, str, str, int]],
    maintenance: Sequence[MaintenanceGap] = (),
    conditions: Sequence[tuple[str, dict[str, int], dict[str, int]]] = (),
) -> str:
    """The report as markdown."""
    passed = sum(c.passed for c in checks)
    out = [
        "# Data-quality report: medallion pipeline",
        "",
        f"Generated {date.today().isoformat()} by `make pipeline-report` "
        "(evals/src/txlens_evals/pipeline_reconciliation.py). Synthetic data only.",
        "",
        f"**Reconciliation: {passed} of {len(checks)} checks passed.**",
        "",
        "## Reconciliation with the ground truth",
        "",
        *table(["Check", "Expected", "Actual", "Result", "Note"],
               [(c.name, c.expected, c.actual, "PASS" if c.passed else "FAIL", c.note)
                for c in checks]),
        "",
        "## Maintenance immunosuppression",
        "",
        PROTOCOL_NOTE,
        "",
        *(table(["Tenant", "MRN", "Drug", "Uncovered days"],
                [(g.tenant_id, g.mrn, g.drug, g.uncovered_days) for g in maintenance[:25]])
          if maintenance else ["Every active patient is covered on every day."]),
        "",
        "## Rows per layer",
        "",
        *table(["Layer", "Table", "Rows"], counts),
        "",
        "## Quarantine reasons",
        "",
        *table(["Entity", "Tenant", "Reason", "Rows"], reasons),
        "",
        "## Mapping coverage (rows with a standard concept)",
        "",
    ]  # fmt: skip
    totals: dict[tuple[str, str], list[int]] = {}
    for t, f, _, mapped, total in cover:
        totals.setdefault((t, f), [0, 0])
        totals[(t, f)][0] += mapped
        totals[(t, f)][1] += total
    out += table(
        ["Table", "Concept field", "Tenant", "Mapped", "Total", "Coverage"],
        [(t, f, tenant, m, n, f"{m / n:.1%}" if n else "-") for t, f, tenant, m, n in cover]
        + [(t, f, "**all**", m, n, f"**{m / n:.1%}**" if n else "-")
           for (t, f), (m, n) in totals.items()],
    )  # fmt: skip
    out += ["", *(render_conditions(conditions) if conditions else [])]
    out += [
        "## Mapping gaps (review queue, largest first)",
        "",
        *table(["Tenant", "Table", "Field", "Source value", "Reason", "Rows"], gaps),
        "",
    ]
    return "\n".join(out)


def build_report(
    spark: SparkSession,
    lake: Lake,
    truth_dir: str,
    vocab_root: str,
    postgres: bool,
    extracted: date,
    review_csv: str = REVIEW_CSV,
) -> tuple[str, list[Check]]:
    """Run every check and render the report; unmapped texts go to `review_csv` (tests pass a
    temporary path so they never rewrite the committed report)."""
    patients = read_truth(spark, truth_dir, "patients")
    outcomes = read_truth(spark, truth_dir, "outcomes")
    n_active, maintenance = maintenance_gaps(lake, patients, outcomes, vocab_root, extracted)
    labs_truth = read_truth(spark, truth_dir, "labs")
    issues = read_truth(spark, truth_dir, "lab_feed_issues")
    checks = [
        *check_patients(lake, patients),
        check_deaths(lake, patients, outcomes),
        *check_labs(lake, labs_truth, issues),
        *check_medications(lake, read_truth(spark, truth_dir, "medications"), vocab_root),
        check_maintenance(n_active, maintenance, extracted),
        check_duplicates(lake, issues, labs_truth),
        check_quarantine(lake, issues),
    ]
    tenants = sorted(r.tenant_id for r in patients.select("tenant_id").distinct().collect())
    pg = postgres_counts(tenants) if postgres else None
    checks.append(check_note_deid(lake))
    if pg is not None:
        checks.append(check_postgres(lake, pg))
        leaks = postgres_note_leaks(tenants)
        if leaks is not None:
            checks.append(Check("notes: no identifier from app.patient_identity in cdm.note "
                                "(Postgres)", 0, leaks, leaks == 0))  # fmt: skip
    reasons = [
        (r.entity, r.tenant_id, r.reason_code, int(r["count"]))
        for r in lake.read("silver", "quarantine")
        .groupBy("entity", "tenant_id", "reason_code").count()
        .orderBy("entity", "tenant_id", "reason_code").collect()
    ]  # fmt: skip
    gaps = [
        (r.tenant_id, r.omop_table, r.concept_field, r.source_value, r.reason, int(r.rows))
        for r in lake.read("gold", "mapping_gaps").orderBy(F.desc("rows"), "source_value")
        .limit(25).collect()
    ]  # fmt: skip
    write_review_csv(lake, vocab_root, review_csv)
    report = render(
        checks, layer_counts(lake), reasons, coverage(lake), gaps, maintenance,
        condition_methods(lake),
    )  # fmt: skip
    return report, checks


def main(argv: list[str] | None = None) -> int:
    """Write the report; exit 1 if any check failed."""
    parser = argparse.ArgumentParser(description="Reconcile pipeline output with ground truth.")
    parser.add_argument("--lake-root", default="lake")
    parser.add_argument("--ground-truth", default="data/ground_truth")
    parser.add_argument("--vocab-root", default="omop/vocab_subset")
    parser.add_argument("--raw-root", default="data/raw")
    parser.add_argument("--out", default="evals/reports/data_quality.md")
    parser.add_argument("--no-postgres", action="store_true")
    args = parser.parse_args(argv)
    from txlens_pipelines.spark import build_spark_session

    spark = build_spark_session("txlens-report")
    try:
        report, checks = build_report(
            spark, Lake(spark, args.lake_root, None), args.ground_truth, args.vocab_root,
            postgres=not args.no_postgres, extracted=read_extract_date(args.raw_root),
        )  # fmt: skip
    finally:
        spark.stop()
    Path(args.out).write_text(report, encoding="utf-8")
    print(report.split("## Rows per layer")[0])
    return 0 if all(c.passed for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
