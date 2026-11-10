# Databricks notebook source
# MAGIC %md
# MAGIC # TxLens 00: setup (run once)
# MAGIC Creates schema `txlens` and volume `data` in your catalog, then unzips the data you uploaded
# MAGIC (`txlens_databricks_data.zip`, made with `make databricks-data`). See docs/DATABRICKS.md.

# COMMAND ----------

# MAGIC %run ./_repo_path

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Catalog")
dbutils.widgets.text("zip_name", "txlens_databricks_data.zip", "Uploaded zip")
catalog = dbutils.widgets.get("catalog")
zip_name = dbutils.widgets.get("zip_name")

# COMMAND ----------

from txlens_pipelines.databricks import setup, unpack

paths = setup(spark, catalog)
print(f"Volume ready: {paths.root}")
print(f"Upload {zip_name} into it now if you have not (Catalog > {catalog} > txlens > data).")

# COMMAND ----------

import os

zip_path = f"{paths.root}/{zip_name}"
if not os.path.exists(zip_path):
    raise FileNotFoundError(f"{zip_path} not found: upload the zip to the volume, then re-run")
folders = unpack(zip_path, paths)
print(f"Unpacked into {paths.root}: {folders}")
assert {"raw", "ground_truth", "vocab_subset"} <= set(folders), folders
