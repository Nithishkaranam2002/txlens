# pipelines/: medallion pipeline (bronze → silver → gold → Postgres)

PySpark + Delta Lake jobs that turn `data/raw` into OMOP CDM v5.4 tables and load them into
Postgres with Row-Level Security on. The same code runs locally (Spark in Docker) and on
Databricks Free Edition ([docs/DATABRICKS.md](../docs/DATABRICKS.md)). Design:
[ADR 0006](../docs/adr/0006-medallion-pipeline.md).

```bash
make spark-build                 # once
make db-setup                    # migrations, app login, tenants
make vocab-subset                # vocabulary subset the pipeline reads
make pipeline                    # bronze, silver, gold, load (LAYERS=bronze,silver to limit)
make pipeline-report             # reconciliation -> evals/reports/data_quality.md
make test-pipelines              # Spark tests in the container
make pipeline-clean              # delete the local lake/ (Postgres untouched)
```

Tests marked `slow` (end-to-end runs on generated data) take several minutes. CI runs them in a
separate `pipelines-e2e` job on pushes to main and on manual runs. Pull requests run only
`pytest -m "not slow"`.

| Layer | Tables | What happens |
|---|---|---|
| bronze | `lab_rows`, `fhir_resources`, `note_files`, `pdf_index` | Raw rows as-is, with `source_file`, `source_row`, `file_sha256`, `ingested_at`, `tenant_id`, `batch_id`. Insert-only. |
| silver | `patients`, `encounters`, `procedures`, `conditions`, `medications`, `labs`, `notes`, `lab_duplicates`, `quarantine` | Dates per center, duplicates removed, units converted, censored values, lab and drug normalization. Bad rows are quarantined with a reason. |
| gold | OMOP `person`, `observation_period`, `visit_occurrence`, `drug_exposure`, `measurement`, `condition_occurrence`, `procedure_occurrence`, `death`, `note`, `cdm_source`; plus `lineage`, `mapping_gaps`, `id_map` | Standard concepts, stable ids, DDL-exact columns. |
| load | Postgres `cdm.*` | Per tenant, one transaction, as the app role with the tenant set. COPY into a stage, then INSERT through RLS. Audit-logged. |

Code map (`src/txlens_pipelines/`):

| Module | Role |
|---|---|
| `runtime.py` | Where tables live (local Delta paths or Unity Catalog), insert-only and per-tenant writes |
| `ids.py` | Stable ids from natural keys (`gold.id_map`) |
| `quality.py` | Quarantine rules and table contracts |
| `vocab.py` | Reads the Athena-format vocabulary subset and our reviewed mappings |
| `codes.py`, `omop_schema.py` | Codes, unit rules and the OMOP column spec (tested against `txlens_omop`) |
| `bronze/`, `silver/`, `gold/`, `load/` | One module per source, entity or step |
| `databricks.py` | Databricks entry points used by `databricks/notebooks/` |
| `cli.py`, `pipeline.py` | `python -m txlens_pipelines` and layer orchestration |
