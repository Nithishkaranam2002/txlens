# Databricks notebook source
# MAGIC %md
# MAGIC # TxLens 01: bronze -> silver -> gold
# MAGIC The same pipeline code as `make pipeline`, writing Delta tables to Unity Catalog:
# MAGIC `<catalog>.txlens_bronze`, `txlens_silver`, `txlens_gold`. Re-running is safe (idempotent).

# COMMAND ----------

# MAGIC %pip install "pandera>=0.26,<1" --quiet

# COMMAND ----------

# MAGIC %run ./_repo_path

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Catalog")
dbutils.widgets.text("layers", "bronze,silver,gold", "Layers")
dbutils.widgets.text("tenants", "", "Tenants (empty = all)")
catalog = dbutils.widgets.get("catalog")
layers = [x.strip() for x in dbutils.widgets.get("layers").split(",") if x.strip()]
tenants = [x.strip() for x in dbutils.widgets.get("tenants").split(",") if x.strip()]

# COMMAND ----------

from txlens_pipelines.databricks import run_pipeline

results = run_pipeline(spark, catalog, layers, tenants or None)
for layer, counts in results.items():
    print(layer, counts)

# COMMAND ----------

# MAGIC %md
# MAGIC Look at the results: open **Catalog > workspace > txlens_gold > person** (Sample Data tab),
# MAGIC or run `SELECT * FROM workspace.txlens_gold.measurement LIMIT 20` in a new cell.
