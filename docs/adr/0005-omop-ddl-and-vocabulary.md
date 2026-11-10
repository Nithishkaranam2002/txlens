# ADR 0005: Pinned official OMOP DDL, two schemas, and a git-ignored vocabulary

**Status:** accepted (2026-11-10)

## Context
We need OMOP CDM v5.4 tables that match the standard exactly (so OHDSI tools and other
researchers recognise them), plus our tenant extension. The official DDL from OHDSI puts every
table in one schema and includes foreign keys from clinical tables into the vocabulary.
The vocabulary itself (SNOMED CT, LOINC, RxNorm, ICD-10-CM, ...) comes from Athena under
licences that do not allow us to redistribute it.

## Decision
- **Pin and keep the official DDL in the repo**: OHDSI CommonDataModel **v5.4.3**
  (commit `746a15e`, Apache-2.0), byte-for-byte, with `SHA256SUMS` and `SOURCE.md` in
  `omop/src/txlens_omop/sql/v5.4.3/`. A test fails if anyone edits them.
- **Render, don't edit**: `txlens_omop.ddl` reads those files and produces our layout. Alembic
  migrations call it with the release pinned (`CDM_RELEASE = "v5.4.3"`).
- **Two schemas**: 13 clinical tables in `cdm`, 8 vocabulary tables in `vocab`.
- **Kept from upstream**: every column and type, primary keys (including where upstream has
  none: `death`, `cdm_source`, `vocabulary`, `concept_relationship`, `concept_ancestor`,
  `concept_synonym`), foreign keys between clinical tables, and indexes.
- **Changed from upstream**: no foreign keys into `vocab`; `CLUSTER` statements dropped;
  `tenant_id` + RLS on clinical tables (ADR 0004); extra indexes for tenant, time-series and
  vocabulary lookups.
- **Vocabulary loading**: streamed from the Athena zip (never unpacked) with `COPY`, filtered to
  SNOMED, LOINC, RxNorm, RxNorm Extension, ICD10CM and small support vocabularies (UCUM, types,
  visits, demographics, metadata). **CPT4 is always excluded** (UMLS licence; Athena ships it
  without names). SNOMED, LOINC and RxNorm are required; other missing vocabularies only warn.
- **Licensing**: `omop/athena/` (the download) and `omop/vocab_subset/` (our extract) are
  git-ignored and docker-ignored. Tests use a hand-made fixture with fake codes and concept ids
  of 2,100,000,000 and above, every name starting with "TEST ONLY".

## Alternatives
- Hand-written DDL or SQLAlchemy models: drifts from the standard, and nobody can prove it
  matches. The schema test compares the database with the pinned upstream files column by column.
- Keep foreign keys into `vocab`: the vocabulary could no longer be truncated and reloaded on its
  own, bulk loads get slower, and an incomplete vocabulary would block clinical loads.
- Load every Athena vocabulary: NDC, SPL and others add millions of rows we do not use.
- Commit a small real vocabulary subset for tests: simpler, but redistributes licensed content.

## Consequences
- Referential integrity to concepts is checked by the pipeline (and `make vocab-check`), not by
  the database. Unknown codes become `concept_id = 0` with the source value kept, and go to review.
- Upgrading to CDM v5.5 means adding a new pinned folder and a new migration; old migrations
  keep rendering v5.4.3.
- Every developer downloads Athena themselves (see `omop/README.md`). The full load takes ~3
  minutes and ~7 GB; the subset is for quick local work.
