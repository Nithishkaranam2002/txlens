# Running the TxLens pipeline on Databricks Free Edition

This guide takes you from nothing to the TxLens bronze, silver and gold tables running on
Databricks. It is written for a first-time Databricks user. It takes about 30 minutes, and most
of that time is waiting for uploads and runs.

**What runs on Databricks:** the same pipeline code as `make pipeline`
(`pipelines/src/txlens_pipelines`), writing Delta tables to Unity Catalog, plus the
reconciliation report. **What does not:** the Postgres load (step 8 of Step 1.4). Databricks
cannot reach the Postgres database on your laptop, so gold stays in Unity Catalog there.

**Data:** only the synthetic data from our generator. Never upload real patient data.

---

## What you need first

- The repo on GitHub (`https://github.com/<your-user>/txlens`).
- On your laptop, data already generated and the vocabulary subset already extracted:

  ```bash
  make generate-data      # data/raw and data/ground_truth
  make vocab-subset       # omop/vocab_subset (needs the Athena download in omop/athena)
  make databricks-data    # builds dist/txlens_databricks_data.zip (about 120 MB)
  ```

  The zip holds three folders: `raw/`, `ground_truth/` and `vocab_subset/`. It uses the small
  vocabulary **subset** (about 20 MB), not the full 7 GB Athena download.

> **Licence note:** `vocab_subset` contains SNOMED, LOINC and RxNorm content from Athena.
> Uploading it to your own private workspace is fine. Do not share the zip or make it public.

---

## Step 1. Create a Free Edition account

1. Go to <https://www.databricks.com/learn/free-edition> and click **Sign up**.
2. Sign up with your email or Google account. No credit card is needed.
3. When the workspace opens, you will see a left sidebar with **Workspace**, **Catalog**,
   **Jobs & Pipelines** and **SQL Editor**.

Free Edition gives you **serverless compute only**. You never create or start a cluster:
notebooks and jobs get compute automatically.

## Step 2. Connect GitHub (only if your repo is private)

If your repo is public, skip this step.

1. Click your user icon (top right) > **Settings** > **Linked accounts**.
2. Under **Git integration**, choose **GitHub** and click **Link Git account**. Follow the
   GitHub sign-in. If you are asked for a token instead, create a fine-grained personal access
   token on GitHub with read access to the txlens repo, and paste it.

## Step 3. Add the repo as a Git folder

1. Sidebar: **Workspace** > open your home folder (`/Workspace/Users/<your email>`).
2. Click **Create** (top right) > **Git folder**.
3. Git repository URL: `https://github.com/<your-user>/txlens`. Provider: **GitHub**.
   Folder name: `txlens`.
4. Click **Create Git folder**. You now have the whole repo at
   `/Workspace/Users/<your email>/txlens`.

To get newer code later, open the Git folder and click the branch name > **Pull**.

## Step 4. Create the schema and the volume (run notebook 00, first part)

1. Open `txlens/pipelines/databricks/notebooks/00_setup`.
2. At the top, check the widget **Catalog** says `workspace`. That is the Free Edition default
   catalog.
3. Click **Run all**. The first cell installs `pandera` (Python restarts, which is normal). The
   first run **stops with an error on purpose**:
   `... txlens_databricks_data.zip not found: upload the zip to the volume`.
   It has already created the schema `workspace.txlens` and the volume `workspace.txlens.data`.

## Step 5. Upload the data zip

1. Sidebar: **Catalog** > **workspace** > **txlens** > **Volumes** > **data**.
2. Click **Upload to this volume**.
3. Choose `dist/txlens_databricks_data.zip` from your laptop and wait for the upload to finish.
   This takes a few minutes for 120 MB.
4. The volume now shows `txlens_databricks_data.zip`.

## Step 6. Unzip it (run notebook 00 again)

1. Go back to `00_setup` and click **Run all** again.
2. The last cell prints `Unpacked into /Volumes/workspace/txlens/data: ['ground_truth', 'raw',
   'vocab_subset']`. Unzipping about 16,000 files can take 5 to 10 minutes.

You only do Steps 4 to 6 once (or again when you regenerate data).

## Step 7. Run the pipeline

You can run it from the notebook (simplest) or as a job (Step 8).

Gold de-identifies the clinical notes. It needs the same key as `DEID_HMAC_KEY` in your
`.env`, stored once as a Databricks secret (with the Databricks CLI):

```
databricks secrets create-scope txlens
databricks secrets put-secret txlens deid_hmac_key
```

1. Open `txlens/pipelines/databricks/notebooks/01_run_pipeline`.
2. Widgets: **Catalog** = `workspace`, **Layers** = `bronze,silver,gold`, **Tenants** = empty
   (all three centers).
3. Click **Run all**. The first cell installs `pandera` (Python restarts, which is normal). The
   run takes about 10 to 20 minutes on serverless.
4. The last cell prints the row counts per layer, for example
   `gold {'person': 2000, 'measurement': 240945, ...}`.

Re-running is safe. Bronze skips files it already has, silver and gold are replaced per
tenant, and ids stay the same.

## Step 8. (Optional) Run it as a job

1. Sidebar: **Jobs & Pipelines** > **Create** > **Job**.
2. Open the three-dot menu (top right) > **Edit as YAML**.
3. Open `pipelines/databricks/job.yml` from the repo, copy everything below the comment lines,
   and paste it in.
4. Replace both `<YOUR_GIT_FOLDER>` with your Git folder path, for example
   `/Workspace/Users/you@example.com/txlens`.
5. Click **Save**, then **Run now**. The job has two tasks: `bronze_silver_gold`, then `report`.
6. Click the run to watch each task and to see the notebook output.

## Step 9. Look at the results

- **Tables:** **Catalog** > **workspace** > `txlens_bronze`, `txlens_silver`, `txlens_gold`.
  Click a table such as `txlens_gold.person` and open **Sample Data**.
- **SQL:** open the **SQL Editor** and try:

  ```sql
  SELECT tenant_id, count(*) FROM workspace.txlens_gold.person GROUP BY tenant_id;

  SELECT reason_code, count(*) FROM workspace.txlens_silver.quarantine GROUP BY reason_code;

  SELECT * FROM workspace.txlens_gold.mapping_gaps ORDER BY rows DESC LIMIT 20;
  ```

## Step 10. Data-quality report

1. Open `txlens/pipelines/databricks/notebooks/02_report` and click **Run all**, or let the
   job's `report` task run it.
2. It shows the report in the notebook and saves it to
   `/Volumes/workspace/txlens/data/reports/data_quality.md`.
3. The first line should read **Reconciliation: 11 of 11 checks passed.** The local report
   has one extra check, Postgres vs gold, which cannot run here.

## Screenshots for the README

Take these after a successful run:

1. **Catalog** showing `txlens_bronze`, `txlens_silver` and `txlens_gold` with their tables.
2. The job run page with both tasks green (Step 8).
3. The output of the last cell of `01_run_pipeline` (row counts per layer).
4. The report in `02_report` showing all checks passed.
5. A SQL Editor result, for example the quarantine reasons query.

Save them in `docs/images/` and link them from the README.

## Troubleshooting

| Problem | What to do |
|---|---|
| `Run this notebook from the txlens Git folder` | Open the notebook from inside your Git folder, not a copy elsewhere. |
| `txlens_databricks_data.zip not found` | Upload the zip to **workspace > txlens > data** (Step 5). The name must match the **Uploaded zip** widget. |
| `PATH_NOT_FOUND .../vocab_subset/...` | Notebook 00 did not finish unzipping. Run it again. |
| `ModuleNotFoundError: pandera` | Run the notebook from the top: the `%pip install` cell must run first. |
| `[SCHEMA_NOT_FOUND]` or permission errors | Keep the catalog `workspace`. Free Edition lets you create schemas there. |
| `ContractViolationError: ... quarantined (x%), limit 5%` | A feed has too many bad rows. Read `txlens_silver.quarantine` to see the reasons. This stop is on purpose. |
| A run is slow or stops on a quota | Free Edition has a daily compute limit. Wait and run again: re-runs only redo what changed. |
| You changed table code and a write fails on schema | Drop the schemas (`DROP SCHEMA workspace.txlens_gold CASCADE`, same for silver and bronze) and run again. |

## How it works (for the curious)

- The notebooks only read widgets and call `txlens_pipelines.databricks.run_pipeline`. All the
  logic is the same Python as the local run.
- `DatabricksRuntime` writes Unity Catalog tables named `<catalog>.txlens_<layer>.<table>`.
  `LocalRuntime` writes Delta folders under `lake/`. Nothing else differs.
- The code avoids features serverless compute does not have: no RDDs, no `cache()`, no
  `input_file_name()`. It uses `try_*` parsing, which works with ANSI SQL mode turned on.
