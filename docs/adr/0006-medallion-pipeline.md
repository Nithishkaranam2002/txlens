# ADR 0006: Medallion pipeline (bronze, silver, gold) on Delta Lake, loaded into Postgres

**Status:** accepted (2026-11-10)

## Context
Three transplant centers send messy data:
- three lab CSV dialects (different delimiters, date formats, units, local codes and typos)
- FHIR bundles that are fully coded, uncoded, or half coded
- free-text notes and scanned pathology PDFs

We need standardized OMOP CDM v5.4 tables (ADR 0005) in Postgres with Row-Level Security
(ADR 0004). Every value must be traceable to the raw row it came from, bad rows must never be
silently dropped or silently loaded, and the same code must run locally and on Databricks Free
Edition (serverless, Python only).

## Decision
- **Three layers in Delta Lake**, each with one job:
  - **bronze**: every raw source as-is, plus lineage (`source_file`, `source_row`,
    `file_sha256`, `ingested_at`, `tenant_id`, `batch_id`). Bronze is insert-only. A file version
    that is already ingested is skipped, and rows are never updated or deleted.
  - **silver**: one clean table per entity (patients, encounters, procedures, conditions,
    medications, labs, notes), plus `lab_duplicates` and `quarantine`. Every row keeps
    `bronze_source_file` and `bronze_source_row`.
  - **gold**: OMOP v5.4 tables, conformed to the pinned DDL, plus `lineage` (OMOP row → bronze
    row), `mapping_gaps` (the mapping review queue) and `id_map`.
- **Load**: gold goes into Postgres `cdm` per tenant, in one transaction. It runs as the
  non-superuser app login with `app.tenant_id` set, so RLS stays on. Postgres does not allow
  `COPY FROM` into a table with RLS, so each table is COPYed into a temporary stage and then moved
  with `INSERT ... SELECT`, which passes the RLS `WITH CHECK` policy. One `audit_log` row is
  written per table.
- **Stable ids from a persistent id map** (`gold.id_map`: tenant, OMOP table, natural key → id).
  Natural keys are tenant + MRN for a person, the source id for FHIR resources, and tenant +
  file + MRN + test + time + value for lab results. A key keeps its id forever, and a new key gets
  the next free number in its tenant's range (`ordinal × 10^8`). Hashing to an integer was
  rejected: OMOP ids are 32-bit, so a hash would collide.
- **Reviewed mappings in OMOP's own `source_to_concept_map`**. This holds lab local codes and
  name variants, drug abbreviations, and uncoded transplant texts. It also holds one TxLens local
  concept (id ≥ 2,000,000,000) for DSA MFI, which has no standard LOINC. Exact matching only,
  never substrings. Drug order: sent RxNorm code → reviewed map → exact ingredient
  name/synonym → Brand Name "Brand name of" ingredient → concept 0.
- **Quality**: a bad *row* goes to `silver.quarantine` with a reason code (`bad_date`,
  `missing_value`, `unparseable_value`, `unknown_unit`, `unmapped_code`, `unknown_patient`), and
  the run continues. A broken *table* contract stops the run before anything is written. Broken
  contracts are:
  - a pandera schema mismatch
  - NULL or duplicate primary keys
  - NULL in an OMOP NOT NULL column
  - a quarantine rate above `max_quarantine_rate` (default 5%)
- **Idempotent re-runs**: silver and gold are rebuilt per tenant (Delta `replaceWhere`), ids
  come from the id map, and Postgres is replaced per tenant.
- **One code base, two runtimes**: transforms are pure DataFrame → DataFrame functions. A
  `Runtime` hides storage: path-based Delta under `lake/` locally, and Unity Catalog tables
  (`<catalog>.txlens_<layer>.<table>`) on Databricks. The code avoids what serverless lacks: no
  RDDs, `cache()` or `input_file_name()`; it uses `try_*` parsing (safe under ANSI SQL).
  Databricks reads the vocabulary **subset**, not the full 7 GB.
- **Reconciliation** (`make pipeline-report`) compares gold, silver and Postgres with the
  generator's ground truth. It checks labs row by row through (file, row), and writes
  `evals/reports/data_quality.md`.

## Alternatives
- **Straight from raw to OMOP in one job**: fewer tables, but no replayable raw copy and no place
  to see what was cleaned, and every fix means re-reading every source.
- **Full overwrite of every layer each run**: simpler, but bronze would lose its history.
  Without an id map, ids would also change whenever rows arrive in a different order.
- **Hashed ids**: no state to keep, but 32-bit OMOP ids collide. A 64-bit hash would need the
  pinned DDL changed.
- **Mapping tables in our own format (CSV / Python dicts)**: quick, but not where OMOP tools
  look. `source_to_concept_map` is the standard place, and `make vocab-check` validates every row.
- **Drop bad rows, or fail on the first one**: dropping hides problems, and failing makes one
  typo stop a whole center. Quarantine with a rate limit keeps both safety and progress.
- **dbt or Delta Live Tables**: good tools, but dbt adds a second language and runtime here, and
  Free Edition allows only one active pipeline per type. Plain PySpark runs identically in both
  places.

## Consequences
- **Production would use incremental Delta `MERGE`** (or change data feed) instead of rebuilding
  silver and gold per tenant. The full rebuild takes about 2 minutes for 2,000 patients and is
  easy to reason about, but it grows with history. The insert-only bronze, the stable id map and
  the natural keys are already what an incremental design needs.
- Lab files are read whole and split into lines, so `source_row` is exact. Very large files
  would need chunking upstream.
- FHIR resources in bronze are compact JSON: the content is unchanged, but whitespace differs
  from the original bytes. The original stays in the raw store, identified by `file_sha256`.
- Problem-list conditions carry no date in the feeds, so they are dated at the transplant (the
  documented assumption).
- Uncoded conditions stay concept 0 (coded FHIR only, as decided). The note NLP step will add
  evidence later.
- Two identical genuine lab rows cannot be told apart from a re-sent duplicate. The report counts
  such cases separately.
