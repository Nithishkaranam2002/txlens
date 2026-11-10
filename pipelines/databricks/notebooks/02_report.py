# Databricks notebook source
# MAGIC %md
# MAGIC # TxLens 02: reconciliation and data-quality report
# MAGIC Compares the gold tables with the ground truth and writes `data_quality.md` to the volume.

# COMMAND ----------

# MAGIC %pip install "pandera>=0.26,<1" --quiet

# COMMAND ----------

# MAGIC %run ./_repo_path

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Catalog")
catalog = dbutils.widgets.get("catalog")

# COMMAND ----------

import os

from txlens_evals.pipeline_reconciliation import Lake, build_report
from txlens_pipelines.databricks import VolumePaths

paths = VolumePaths(catalog)
report, checks = build_report(
    spark,
    Lake(spark, None, catalog),
    paths.ground_truth,
    paths.vocab,
    postgres=False,
)
os.makedirs(paths.reports, exist_ok=True)
with open(f"{paths.reports}/data_quality.md", "w", encoding="utf-8") as out:
    out.write(report)
displayHTML(f"<pre>{report}</pre>")
failed = [c.name for c in checks if not c.passed]
assert not failed, f"Failed checks: {failed}"
