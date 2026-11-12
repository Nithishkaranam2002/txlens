"""Copilot data access: de-identified aggregate views, a read-only role, released counts.

- Schema `copilot`: views over cdm for the copilot's SQL tool. They have no free text, no
  source values and no calendar dates: every date becomes `day` (days after the transplant)
  and `year`. Patients are only reachable through `person_id`, which the SQL guard never lets
  a query output or group by.
- `txlens_copilot_owner` (NOLOGIN, no BYPASSRLS) owns the views and may read cdm. The views run
  with its rights, so cdm's tenant policy still applies (it keys on the session's tenant, not
  on the role).
- `txlens_copilot` (NOLOGIN) may read only the copilot views and two vocabulary tables. The API
  switches to it (`SET LOCAL ROLE`) to run generated SQL, so even SQL that slipped past the
  guard could not read app.* (identities, PHI spans, users) or raw cdm. txlens_app is granted
  it WITHOUT INHERIT: the app never gains its rights implicitly, it can only switch to it.
- `set_config()` is revoked from PUBLIC and granted back to txlens_app only, so the copilot
  role could not switch `app.tenant_id` (RLS's tenant) even with SQL that slipped past the
  guard. Changing pg_catalog grants needs a superuser; elsewhere this step only warns.
- `app.released_count`: every count shown to a user (cohort builder, survival, copilot), kept
  for the differencing guard (services/disclosure.py).

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')"
OWNER, READER = "txlens_copilot_owner", "txlens_copilot"
CDM_READ = ("person", "procedure_occurrence", "condition_occurrence", "measurement",
            "drug_exposure", "observation", "visit_occurrence", "death")  # fmt: skip

SET_CONFIG = "pg_catalog.set_config(text, text, boolean)"

# Codes as in txlens_backend.clinical_codes (repeated: a migration must not change later).
PATIENT = """
CREATE VIEW copilot.patient AS
WITH transplant AS (
    SELECT DISTINCT ON (po.person_id)
        po.person_id, po.procedure_date AS transplant_date,
        CASE a.concept_code WHEN '70536003' THEN 'kidney' ELSE 'heart' END AS organ,
        CASE pc.concept_code WHEN '175901007' THEN 'living'
                             WHEN '175902000' THEN 'deceased' END AS donor_type
    FROM cdm.procedure_occurrence po
    JOIN vocab.concept pc ON pc.concept_id = po.procedure_concept_id
    JOIN vocab.concept_ancestor ca ON ca.descendant_concept_id = po.procedure_concept_id
    JOIN vocab.concept a ON a.concept_id = ca.ancestor_concept_id
    WHERE a.vocabulary_id = 'SNOMED' AND a.concept_code IN ('70536003', '32413006')
    ORDER BY po.person_id, po.procedure_date
),
graft_failure AS (
    SELECT co.person_id, min(co.condition_start_date) AS failure_date
    FROM cdm.condition_occurrence co
    JOIN vocab.concept_ancestor ca ON ca.descendant_concept_id = co.condition_concept_id
    JOIN vocab.concept a ON a.concept_id = ca.ancestor_concept_id
    WHERE a.vocabulary_id = 'SNOMED' AND a.concept_code IN ('236583003', '233934000')
    GROUP BY co.person_id
)
SELECT
    p.person_id,
    CASE p.gender_concept_id WHEN 8507 THEN 'male' WHEN 8532 THEN 'female' END AS sex,
    t.organ, t.donor_type,
    extract(year FROM t.transplant_date)::int AS transplant_year,
    (extract(year FROM t.transplant_date) - p.year_of_birth)::int AS age_at_transplant,
    g.failure_date IS NOT NULL AS graft_loss,
    (g.failure_date - t.transplant_date) AS graft_loss_day,
    d.death_date IS NOT NULL AS died,
    (d.death_date - t.transplant_date) AS death_day
FROM cdm.person p
JOIN transplant t ON t.person_id = p.person_id
LEFT JOIN graft_failure g ON g.person_id = p.person_id
LEFT JOIN cdm.death d ON d.person_id = p.person_id
"""
# The transplant date of each patient, for the day offsets (not exposed itself).
TRANSPLANT_DAY = """
    JOIN (SELECT DISTINCT ON (po.person_id) po.person_id, po.procedure_date AS tx
          FROM cdm.procedure_occurrence po
          JOIN vocab.concept_ancestor ca ON ca.descendant_concept_id = po.procedure_concept_id
          JOIN vocab.concept a ON a.concept_id = ca.ancestor_concept_id
          WHERE a.vocabulary_id = 'SNOMED' AND a.concept_code IN ('70536003', '32413006')
          ORDER BY po.person_id, po.procedure_date) t ON t.person_id = x.person_id
"""
EVENT_VIEWS = {
    "condition": ("cdm.condition_occurrence", "condition_start_date",
                  "x.condition_concept_id, x.condition_type_concept_id"),
    "procedure": ("cdm.procedure_occurrence", "procedure_date", "x.procedure_concept_id"),
    "drug_exposure": ("cdm.drug_exposure", "drug_exposure_start_date",
                      "x.drug_concept_id, x.days_supply"),
    "observation": ("cdm.observation", "observation_date",
                    "x.observation_concept_id, x.value_as_concept_id, x.value_as_number"),
    "visit": ("cdm.visit_occurrence", "visit_start_date", "x.visit_concept_id"),
}  # fmt: skip
MEASUREMENT = f"""
CREATE VIEW copilot.measurement AS
SELECT x.person_id, x.measurement_concept_id,
       CASE WHEN lc.vocabulary_id = 'TxLens' AND lc.concept_code = 'DSA_MAX_MFI' THEN 'dsa_mfi'
            WHEN c.vocabulary_id = 'LOINC' THEN CASE c.concept_code
                WHEN '2160-0' THEN 'creatinine' WHEN '98979-8' THEN 'egfr'
                WHEN '77348-1' THEN 'tacrolimus_trough' WHEN '103142-6' THEN 'dd_cfdna' END
       END AS lab,
       x.value_as_number, x.value_as_concept_id, x.unit_concept_id,
       (x.measurement_date - t.tx) AS day, extract(year FROM x.measurement_date)::int AS year
FROM cdm.measurement x
LEFT JOIN vocab.concept c ON c.concept_id = x.measurement_concept_id
LEFT JOIN vocab.concept lc ON lc.concept_id = x.measurement_source_concept_id
{TRANSPLANT_DAY}
"""


def event_view(name: str, table: str, date_column: str, columns: str) -> str:
    """A cdm event table as person, concepts/values, day after transplant and year."""
    return (
        f"CREATE VIEW copilot.{name} AS SELECT x.person_id, {columns}, "
        f"(x.{date_column} - t.tx) AS day, extract(year FROM x.{date_column})::int AS year "
        f"FROM {table} x {TRANSPLANT_DAY}"
    )


def create_role(role: str) -> None:
    """A cluster-wide NOLOGIN role without RLS bypass, unless another database made it."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
                CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS;
            END IF;
        END
        $$
        """
    )


def upgrade() -> None:
    create_role(OWNER)
    create_role(READER)
    op.execute("CREATE SCHEMA copilot")
    op.execute(f"GRANT USAGE ON SCHEMA cdm, vocab, copilot TO {OWNER}")
    op.execute(f"GRANT SELECT ON {', '.join(f'cdm.{t}' for t in CDM_READ)} TO {OWNER}")
    op.execute(f"GRANT SELECT ON vocab.concept, vocab.concept_ancestor TO {OWNER}")
    views = ["patient", "measurement", *EVENT_VIEWS]
    op.execute(PATIENT)
    op.execute(MEASUREMENT)
    for name, (table, date_column, columns) in EVENT_VIEWS.items():
        op.execute(event_view(name, table, date_column, columns))
    for view in views:
        op.execute(f"ALTER VIEW copilot.{view} OWNER TO {OWNER}")
    op.execute(f"GRANT USAGE ON SCHEMA copilot, vocab TO {READER}")
    op.execute(f"GRANT SELECT ON {', '.join(f'copilot.{v}' for v in views)} TO {READER}")
    op.execute(f"GRANT SELECT ON vocab.concept, vocab.concept_ancestor TO {READER}")
    op.execute(f"GRANT {READER} TO txlens_app WITH INHERIT FALSE")
    set_config_grants("REVOKE EXECUTE ON FUNCTION {f} FROM PUBLIC; "
                      "GRANT EXECUTE ON FUNCTION {f} TO txlens_app")  # fmt: skip

    op.execute(
        f"""
        CREATE TABLE app.released_count (
            release_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_id text NOT NULL DEFAULT {CURRENT_TENANT} REFERENCES app.tenant (tenant_id),
            user_id uuid NOT NULL REFERENCES app.app_user (user_id),
            released_at timestamptz NOT NULL DEFAULT now(),
            source text NOT NULL,
            family text NOT NULL,
            base text NOT NULL,
            cell text NOT NULL,
            constraints jsonb NOT NULL,
            count integer NOT NULL CHECK (count >= 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_released_count_lookup ON app.released_count "
        "(tenant_id, user_id, family, base, cell, released_at)"
    )
    op.execute("ALTER TABLE app.released_count ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE app.released_count FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON app.released_count "
        f"USING (tenant_id = {CURRENT_TENANT}) WITH CHECK (tenant_id = {CURRENT_TENANT})"
    )
    op.execute("GRANT SELECT, INSERT, DELETE ON app.released_count TO txlens_app")


def set_config_grants(statements: str) -> None:
    """Run grant statements on set_config() as a superuser; only warn otherwise."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
                {statements.format(f=SET_CONFIG)};
            ELSE
                RAISE WARNING 'not a superuser: set_config() stays executable by PUBLIC';
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    set_config_grants("GRANT EXECUTE ON FUNCTION {f} TO PUBLIC")
    op.execute("DROP TABLE app.released_count")
    op.execute(f"REVOKE {READER} FROM txlens_app")
    op.execute("DROP SCHEMA copilot CASCADE")
    for role in (READER, OWNER):
        op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA cdm, vocab FROM {role}")
        op.execute(f"REVOKE USAGE ON SCHEMA cdm, vocab FROM {role}")
    # The roles are cluster-wide and may be used by other databases: they are kept.
