# ADR 0004: Multi-tenancy with tenant_id + Postgres Row-Level Security

**Status:** accepted (2026-11-10)

## Context
Each tenant is one transplant center. Centers must never see each other's patients, even if
the application has a bug (a forgotten `WHERE tenant_id = ...`). The data model is OMOP CDM,
which has no notion of tenants. The shared OMOP vocabulary (~7 GB with the full Athena
download) should exist once, not once per center. We expect a handful of centers, not
thousands, and one team runs the platform.

## Options compared

| | **RLS + tenant_id** (chosen) | **Schema per tenant** | **Database per tenant** |
|---|---|---|---|
| Isolation | Database enforces it on every query, including direct SQL | Depends on `search_path` being set right | Strongest: separate connections, separate files |
| Shared vocabulary | One copy | One copy (cross-schema) | One copy per database (~7 GB each), or an extra service |
| Migrations | Run once | Run once per schema | Run once per database |
| Cross-center research (de-identified) | Possible with a controlled path | Needs UNION across schemas | Needs a separate warehouse |
| Adding a center | Insert a row in `app.tenant` | Create schema + run migrations | Create database + migrate + load vocab |
| Per-center backup/restore, or "delete all of center X" | Harder (filter by tenant_id) | Easy | Easiest |
| Noisy neighbour | Shared tables and indexes | Shared database | Isolated |
| Main risk | Policy or role misconfiguration | Wrong `search_path` = wrong data | Operational cost |

## Decision
- Every clinical table in `cdm` gets `tenant_id text NOT NULL` referencing `app.tenant`. This is
  a **deliberate extension of standard OMOP**; vocabulary tables in `vocab` have no tenant.
- Row-Level Security is **enabled and forced** on every clinical table, with one policy:
  `tenant_id = NULLIF(current_setting('app.tenant_id', true), '')` for reads and writes.
  No tenant set (or an empty one) matches no rows.
- `tenant_id` defaults to the session's tenant, so inserts cannot forget it.
- The app connects as a login user in role **`txlens_app`**: not a superuser and `NOBYPASSRLS`.
  Superusers always bypass RLS, so the migration owner is never used for app queries.
- The tenant is set per transaction with `set_config('app.tenant_id', x, true)`, so a pooled
  connection never carries one request's tenant into the next.
- Tests run as `SET ROLE txlens_app` and as a real login user, against every clinical table.

## Consequences
- Isolation holds even for hand-written SQL in the copilot's text-to-SQL path, as long as that
  path uses the app role (it must).
- Every index used for patient lookups includes `tenant_id` (`ix_<table>_tenant`).
- **Known limit:** foreign-key checks run outside RLS. A tenant inserting a row that points at
  another tenant's `person_id` gets a foreign-key error instead of "not found", which reveals
  that the id exists (never any data). Ids are global and not meaningful. Closing this fully
  would need tenant-scoped composite foreign keys, which means changing the official OMOP keys.
- The researcher role (de-identified, possibly cross-center) will need its own views and role;
  it must not be a superuser either.
- Per-center export or deletion is a filtered operation, not a `DROP SCHEMA`.
