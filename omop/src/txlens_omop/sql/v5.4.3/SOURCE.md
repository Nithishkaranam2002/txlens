# Source of these files

Official OMOP CDM v5.4 PostgreSQL DDL from the OHDSI CommonDataModel repository.
The four `.sql` files are byte-for-byte copies. **Do not edit them**: a test checks them
against `SHA256SUMS`. All changes (schema split, tenant columns, RLS) are made by
`txlens_omop/ddl.py` and the Alembic migrations, never in these files.

| | |
|---|---|
| Repository | https://github.com/OHDSI/CommonDataModel |
| Release | `v5.4.3` (2026-08-04) |
| Commit | `746a15e0fb36a95ba6cc0993737f1273bbad92f2` |
| Path | `inst/ddl/5.4/postgresql/` |
| License | Apache License 2.0 (Copyright OHDSI). Redistribution with attribution is allowed. |
| Downloaded | 2026-11-10 |

Git blob SHAs match the upstream tree:

| File | Git blob SHA |
|---|---|
| `OMOPCDM_postgresql_5.4_ddl.sql` | `162bdc5c863fec8ca32e07814a40a347f267788a` |
| `OMOPCDM_postgresql_5.4_primary_keys.sql` | `9304d020bcb007bca8ad8e57873b76a2b626e8d9` |
| `OMOPCDM_postgresql_5.4_indices.sql` | `18b739493af0c3bcc90bff728e987a5b06a7bb0b` |
| `OMOPCDM_postgresql_5.4_constraints.sql` | `19818c3cd59fa47d153160197a8be9399040a6b6` |

To re-download one file:

```bash
gh api "repos/OHDSI/CommonDataModel/contents/inst/ddl/5.4/postgresql/OMOPCDM_postgresql_5.4_ddl.sql?ref=746a15e0fb36a95ba6cc0993737f1273bbad92f2" \
  --jq .content | base64 -d > OMOPCDM_postgresql_5.4_ddl.sql
```
