"""Columns of app.mapping_gap, shared by the gold job (Spark, also on Databricks) and the
Postgres load (psycopg), so it imports neither."""

# In load order; loaded_at is filled in by Postgres.
REVIEW_GAP_COLUMNS = ("tenant_id", "source_value", "reason", "rows", "suggested_concept_id",
                      "suggestion_score", "review_reason")  # fmt: skip
