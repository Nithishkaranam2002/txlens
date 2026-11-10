# LEARNING.md

Plain-English notes on what was built, why, and how to explain it in an interview.

## Phase 1, Step 1.1 — Project skeleton

### What was built
- The folder layout for the whole project.
- One **uv workspace** with four Python packages: `backend`, `data_generator`, `pipelines`,
  `evals`. They share one lockfile (`uv.lock`), so every package uses the same library versions.
- Shared quality tools: **ruff** (style and common bugs), **mypy strict** (types), **pytest**.
- A **Next.js** app in `frontend/` with **Vitest** for tests.
- `infra/docker-compose.yml`: Postgres 16 + pgvector, Temporal dev server (with its web UI),
  Langfuse v2, and a Spark runner container.
- A **Makefile** so every common task is one command, and **GitHub Actions** CI that runs
  lint + tests on every push.

### Why these tools
- **uv workspace**: very fast installs, one lockfile, reproducible builds.
- **Spark in Docker**: Spark needs Java 17; the container brings it, so the Mac does not.
  See `docs/adr/0001-spark-in-docker.md`.
- **Temporal dev server**: one small container (SQLite inside) with the UI built in; no separate
  Temporal database or UI container needed locally.
- **Langfuse v2**: only needs Postgres, so it shares our Postgres. See `docs/adr/0002`.

### Trade-offs
- Langfuse v2 is older than v3 but needs far less disk.
- Spark tests do not run in plain `make test` on the Mac (they need Java); they run in the
  container (`make test-pipelines`) and in CI.
- We capped `importlib-metadata` below 10 because its newest release added an unknown,
  unverified dependency — a small example of supply-chain caution.

### Likely interview questions
1. **Why a monorepo with a uv workspace?** Backend, pipelines and evals share code and
   versions. One lockfile means "works on my machine" also works in CI.
2. **Why run Spark in Docker?** Spark needs a specific Java version. The container pins
   Python 3.12 + Java 17 + Spark 3.5, the same on every machine.
3. **How do you keep secrets safe?** Only in `.env`, which git ignores. The repo has
   `.env.example` with placeholders, and settings use `SecretStr` so keys never show in logs.
4. **What does CI check?** Formatting, lint, strict types, Python tests including Spark,
   and frontend lint, types and tests on every push and pull request.
5. **Why pin Docker image versions?** So the stack doesn't change under you when a new image
   is published; upgrades become deliberate, reviewable changes.

## Phase 1, Step 1.2 — Synthetic transplant data generator

### What was built
- `data_generator/` (`txlens_datagen`): a seeded generator of **synthetic** transplant patients.
  Default: 2,000 patients across 3 fictional centers (tenants), ~85% kidney, ~15% heart.
- `make generate-data` writes `data/raw/` (messy inputs) and `data/ground_truth/` (correct
  answers), plus `data/manifest.json` with the seed and a summary. About 240 MB, ~1 minute.
- `make generate-data-scale` writes 20,000 patients (no PDFs, no note text) to `data_scale/`
  for Spark volume tests (~800 MB, under a minute).
- The CLI prints a summary: patients per center/organ, rejections by type, injury false
  alarms, graft losses, deaths, lab rows, notes, PDFs and total size.

| Raw file (messy) | What makes it messy |
|---|---|
| `raw/labs/center_{a,b,c}_labs.csv` | Different columns per center; creatinine in mg/dL or µmol/L; dd-cfDNA as `0.32%`, a fraction, or `<0.05`; ISO, day-first and US dates; decimal commas (B); LOINC codes on some rows only (A); duplicates, bad dates, test-name typos, whitespace, missing values |
| `raw/fhir/<center>/<patient>.json` | FHIR-like bundles; A codes everything, B uses free text and `M`/`F`, C is mixed; drug names as generic, brand or abbreviation |
| `raw/notes/<center>/*.txt` | Abbreviations (Cr, tacro, Tx, DGF), negations, past/family history, hypothetical plans |
| `raw/pathology/<center>/*.pdf` | Image-only "scans": slight rotation, blur, grain, speckles; needs OCR |

Ground truth: `patients`, `outcomes`, `medications`, `labs` (standard units, with the raw
file and row each came from), `lab_feed_issues` (every injected problem), `rejection_episodes`,
`injury_episodes`, `biopsies`, `encounters`, `surveillance_schedule`, `notes` and
`note_facts.jsonl` (each fact with its exact character span and negation/temporality/
experiencer flags).

### The clinical ideas, in plain English
(Numbers are rough real-world ranges used for realism only, not medical advice.)
- **Transplant and donor type.** A kidney comes from a deceased donor (~65%) or a living
  donor (~35%). Living-donor kidneys usually work better at the start. Hearts always come
  from deceased donors.
- **Delayed graft function (DGF).** The new kidney is slow to start, so the patient needs
  dialysis in the first week. More common with deceased donors. In the data: very high
  creatinine in the first weeks that then falls.
- **Immunosuppression.** Drugs that stop the immune system from attacking the new organ:
  **tacrolimus** (tacro, FK506, Prograf), **mycophenolate mofetil** (MMF, CellCept) and
  **prednisone** (a steroid tapered down to ~5 mg a day). Tacrolimus is dosed by blood
  level: the **trough** (lowest level, just before the next dose) should be about 8–12 ng/mL
  in the first months, then 5–8. Some values are out of range on purpose.
- **Creatinine and eGFR.** Creatinine is a waste product the kidneys clear; if it rises,
  the kidney is working less well. eGFR is an estimate of kidney filtering calculated from
  creatinine, age and sex (we use the 2021 CKD-EPI equation). Stable transplant kidneys sit
  around creatinine 0.9–1.8 mg/dL and eGFR 40–80. Some labs report creatinine in µmol/L
  (multiply mg/dL by 88.4). Heart patients get these labs too, because tacrolimus can slowly
  harm the kidneys.
- **dd-cfDNA (donor-derived cell-free DNA).** Small pieces of DNA from the donor organ float
  in the patient's blood. When the organ is injured, more donor DNA leaks out, so the
  percentage rises — often weeks before creatinine moves. Kidney: usually 0.1–0.5%; ≥0.5%
  means possible injury, ≥1% high risk of rejection. Heart: usually below ~0.15%, and ~0.2%
  is already a warning. Tests are scheduled at months 1, 2, 3, 4, 6, 9, 12, then every
  3 months; about 7% are missed.
- **Not every rise is rejection.** BK virus nephropathy, CMV infection and acute tubular
  injury (ATN) also damage the kidney and raise dd-cfDNA. The biopsy then says "no
  rejection". These are labeled `injury_episodes` — realistic false alarms that the
  surveillance engine must not call rejection.
- **Donor-specific antibodies (DSA).** Antibodies the patient makes against the donor's
  tissue type. Measured as MFI; ≥1000 counts as positive. Uncommon overall, common in ABMR.
- **Rejection types.** **TCMR** (T-cell mediated): immune cells attack the organ; usually
  responds to steroids. **ABMR** (antibody-mediated): antibodies attack the blood vessels;
  harder to treat (plasmapheresis, IVIG), higher dd-cfDNA, more graft loss. **Borderline**:
  suspicious but mild changes. **Mixed**: both TCMR and ABMR. In the data, dd-cfDNA and
  creatinine start rising 2–8 weeks before the biopsy; ~75% respond to treatment and their
  values fall back, the rest stay high.
- **Biopsy grading.** Kidney biopsies use the **Banff** scores (i = inflammation,
  t = tubulitis, v = arteritis, g = glomerulitis, ptc = capillaritis, ci/ct = scarring,
  C4d = an antibody footprint stain); TCMR gets a grade like IA, IB or IIA. Heart biopsies use
  **ISHLT** grades: 0R (none) to 3R (severe) for cellular rejection, and pAMR 0–3 for
  antibody-mediated rejection.
- **Outcomes.** **Graft loss** means the organ stopped working (back to dialysis for kidney;
  for heart we treat it as death). ~6.5% by 3 years, more after ABMR or unresolved
  rejection. **Death with a functioning graft**: the patient dies of another cause while
  the organ still works.

### Why these tools
- **numpy random `SeedSequence`**: one master seed, and an independent stream per patient
  and per part, so results are reproducible and stable when the cohort size changes.
- **Pillow**: draws the pathology report and adds scan effects; saves a JPEG-compressed PDF.
  No extra PDF library needed. We set the PDF date ourselves so files are byte-identical.
- **Standard-library `csv`/`json`** for outputs: no pandas needed just to write files.
- See `docs/adr/0003-synthetic-data-truth-first.md` for the "truth first" design.

### Trade-offs
- Simplified medicine: at most one early and one late rejection, simple dose steps, simple
  curves. Good enough to test software, not to study transplant outcomes.
- The notes come from templates, so they are more regular than real notes. NLP scores on
  them will be optimistic; the evals must say so.
- PDFs at 120 dpi keep the dataset small; OCR is a bit harder than on 300 dpi scans.

### Likely interview questions
1. **Why build your own generator instead of using Synthea?** We need transplant-specific
   signals (dd-cfDNA timing, Banff grades, rejection types) and controlled mess, each with
   a known right answer, so every pipeline step can be scored.
2. **How do you know the raw files and the ground truth agree?** Both are rendered from the
   same in-memory "truth" objects, and a test parses each center's raw CSV back and checks
   every value, date and unit conversion against the ground truth.
3. **How is it reproducible?** One seed, a separate random stream per patient and part, no
   use of "today", fixed PDF dates. A test generates twice and compares file hashes.
4. **Why include BK/CMV/ATN episodes?** dd-cfDNA rises with any graft injury, not only
   rejection. A surveillance system that ignores this will raise false alarms; the ground
   truth lets us measure that.
5. **How does it scale?** It streams one patient at a time to open files: 20,000 patients
   in under a minute with about 50 MB of memory.

## Phase 1, Step 1.3 — OMOP CDM database

### What was built
- **Postgres tables for OMOP CDM v5.4**, created by Alembic migrations (`make db-migrate`) from
  the official OHDSI DDL (release v5.4.3, kept unedited in `omop/src/txlens_omop/sql/`).
  13 clinical tables live in schema `cdm`, 8 vocabulary tables in schema `vocab`.
- **Multi-tenancy**: every clinical table has a `tenant_id` (one tenant = one transplant center)
  and Row-Level Security, so a center only ever sees its own rows.
- **Vocabulary loader** (`make vocab-load`): reads the Athena zip without unpacking it and
  bulk-loads ~4 million concepts and ~62 million relationship/ancestor rows in about 3 minutes.
- **Subset extractor** (`make vocab-subset`) for a small local vocabulary.
- **Code check** (`make vocab-check`): every code the project uses is checked against the real
  vocabulary. It found one imprecise code (tacrolimus trough), which was fixed.

### OMOP in plain English
- **OMOP CDM** (Common Data Model) is a standard set of tables for patient data, used by
  hundreds of hospitals and research networks (OHDSI). If our data is in OMOP, standard tools
  and other researchers' queries work on it unchanged.
- **The main tables**: `person` (one row per patient), `visit_occurrence` (visits and stays),
  `condition_occurrence` (diagnoses), `drug_exposure` (medications), `measurement` (lab
  results such as creatinine and dd-cfDNA), `procedure_occurrence` (transplant, biopsy),
  `observation` (other facts), `death`, `note` and `note_nlp` (clinical text and what NLP found
  in it), `specimen` (biopsy tissue), `observation_period` (when we can see the patient).
- **concept_id**: OMOP never stores a diagnosis as text like "kidney rejection". It stores a
  number, a **concept_id**, that points to a row in `vocab.concept` with the name, the
  vocabulary (SNOMED, LOINC, ...), the original code and the domain. Example:
  `3016723` = LOINC `2160-0` "Creatinine [Mass/volume] in Serum or Plasma".
- **Source vs standard concepts.** A *source* concept is the code exactly as the hospital sent
  it, for example ICD-10-CM `T86.11` "Kidney transplant rejection" (concept_id 45609393). A
  *standard* concept is the one OMOP uses for analysis, for example SNOMED `236570004`
  "Renal transplant rejection" (concept_id 4128369). Each clinical row stores both: the
  standard one in `condition_concept_id`, the source one in `condition_source_concept_id`, and
  the raw text in `condition_source_value`. Standard concepts have `standard_concept = 'S'`.
- **"Maps to"**: the relationship in `concept_relationship` that leads from a source concept to
  its standard concept. `T86.11 —Maps to→ 236570004`. If a code has no valid "Maps to", we
  store `concept_id = 0`, keep the source value, and send it to human review.
- **concept_ancestor**: a precomputed table of every ancestor → descendant pair in the
  hierarchy. That is why a search for SNOMED "Transplanted organ rejection" (4309320) finds all
  57 of its sub-types in one query, including "Renal transplant rejection", "Acute rejection of
  renal transplant - grade II" and "Cardiac transplant rejection". No recursive query needed.
- **Domains** tell you which table a concept belongs in: Condition → `condition_occurrence`,
  Measurement → `measurement`, Drug → `drug_exposure`, Procedure → `procedure_occurrence`.
  A code with the wrong domain would land in the wrong table, which is why the code check
  checks domains.
- **Vocabularies we load**: SNOMED (conditions, procedures), LOINC (labs), RxNorm and RxNorm
  Extension (drugs), ICD-10-CM (diagnosis codes as hospitals bill them), UCUM (units) and small
  ones for types, visits and demographics. CPT4 is excluded (separate licence).
- **Why the vocabulary is not on GitHub**: SNOMED and the other vocabularies are licensed. We
  may use them, but not redistribute them. The download stays in the git-ignored `omop/athena/`;
  tests use a fake fixture with obviously fake codes.

### Why Row-Level Security (RLS)
- RLS is a Postgres feature: each table gets a **policy**, a condition Postgres adds to every
  query automatically. Ours is "`tenant_id` must equal the tenant set for this transaction".
- So even a query that forgets `WHERE tenant_id = ...`, or SQL written by the AI copilot, can
  only see the current center's rows. No tenant set means no rows at all.
- Two traps we handled: **superusers skip RLS**, so the app uses a separate, limited role
  (`txlens_app`); and **connection pools reuse connections**, so the tenant is set per
  transaction (`set_config(..., true)`), never per connection.
- See ADR 0004 for the comparison with schema-per-tenant and database-per-tenant.

### Why these tools
- **Alembic**: versioned, reviewable database changes that run the same way locally and in CI.
- **Pinned official DDL** instead of hand-written tables: a test proves the database matches the
  OHDSI standard column by column.
- **psycopg 3 `COPY`**: the fastest way to bulk-load into Postgres; streaming from the zip keeps
  memory near 0.5 GB for a 4.9 GB download.

### Trade-offs
- No foreign keys from clinical tables to the vocabulary: the vocabulary can be reloaded on its
  own, but concept validity is checked by our pipeline instead of the database.
- One shared set of tables for all centers is simple and efficient, but per-center backup or
  deletion is a filtered job, and a foreign-key error could reveal that another center's id
  exists (never its data).
- The full vocabulary costs ~7 GB of disk; the subset exists for quick work.

### Likely interview questions
1. **What is the difference between a source and a standard concept?** The source concept is
   the code as the hospital sent it (ICD-10-CM T86.11); the standard concept is the one OMOP
   analyses use (SNOMED 236570004). "Maps to" links them; we store both so nothing is lost.
2. **How do you find every kind of transplant rejection?** Look up the parent concept and join
   `concept_ancestor`: one query returns all descendants, whatever their depth.
3. **How do you stop one hospital seeing another's patients?** `tenant_id` on every clinical
   table plus Postgres Row-Level Security, with the app on a non-superuser role and the tenant
   set per transaction. Tests try direct SELECTs, inserts and updates across tenants.
4. **Why not a schema or database per hospital?** More migrations, more copies of a 7 GB
   vocabulary, harder cross-center research. RLS gives database-enforced isolation with one
   copy of everything; the ADR lists when the other options would win.
5. **What happens with a code you don't recognise?** `concept_id = 0`, the original code is
   kept in the `*_source_value` column, and the record goes to the human review queue.

## Phase 1, Step 1.4 — Medallion pipelines (bronze → silver → gold → Postgres)

### What was built
- **A PySpark + Delta Lake pipeline** (`make pipeline`). It turns the three centers' messy
  files into OMOP tables and loads them into Postgres:
  - 249,517 raw lab rows become 240,945 clean results
  - 22,234 medication orders, 65 deaths and 12,505 notes
  - all of it as OMOP rows
- **Bronze** keeps every raw row exactly as received, with where it came from (file, row
  number, file fingerprint, time, tenant, run id). It is insert-only: running again adds nothing.
- **Silver** cleans the data:
  - reads each center's date format
  - removes exact duplicate lines
  - converts creatinine µmol/L to mg/dL
  - handles `<0.05`, `>90`, `0,0019` and `0.23%`
  - maps lab names and drug names to standard codes

  Bad rows go to a **quarantine** table with a reason.
- **Gold** builds the OMOP tables:
  - **Stable ids:** from an id map, so a re-run never changes them.
  - **Lineage:** a lineage table points every OMOP row back to its raw row.
  - **Mapping gaps:** a mapping-gaps table is the review queue for codes we could not map.
- **Postgres load** runs as the limited app login with the tenant set, so Row-Level Security
  stays on. Each load is written to `audit_log`.
- **Reconciliation report** (`make pipeline-report`) proves the result against the generator's
  ground truth. 12 of 12 checks pass; see `evals/reports/data_quality.md`.
- **Databricks**: the same code runs on Databricks Free Edition. See `docs/DATABRICKS.md`.

### The medallion idea in plain English
- **Bronze = the photocopy.** Never change what the hospital sent. If we find a bug later, we
  re-run from bronze instead of asking the hospital to send everything again.
- **Silver = the cleaned version.** One tidy table per thing (patients, labs, medications). Same
  units, real dates, known codes. Anything we cannot fix goes to quarantine with a reason code
  (`bad_date`, `missing_value`, `unknown_unit`, `unmapped_code`, ...). It is never silently
  dropped and never silently loaded.
- **Gold = the product.** The standard OMOP tables that analysts, the API and the AI copilot
  use.
- **Two kinds of failure:**
  - **A bad row** (one typo): the row is quarantined and the run continues.
  - **A broken table:** the run stops before writing anything. A broken table means wrong
    columns, duplicate ids, or more than 5% of rows quarantined.
- **Idempotent** means running the pipeline twice gives the same result as running it once. No
  duplicate rows, and the same ids.

### How the tricky parts work
- **Drug names**: "Prograf", "tacro", "FK506" and "tacrolimus" are all tacrolimus. We try, in
  order:
  1. the RxNorm code the center sent
  2. a *reviewed* mapping table (OMOP's `source_to_concept_map`)
  3. an exact ingredient name
  4. a brand name, followed through RxNorm's "Brand name of" link to its ingredient

  We never match pieces of words. The reviewed table comes first because "mycophenolate"
  exactly matches a *different* RxNorm ingredient than the mycophenolate mofetil the data means.
- **Stable ids**: OMOP ids are 32-bit numbers, so hashing would collide. Instead, `gold.id_map`
  remembers the id given to each key (for example center + MRN) and gives new keys the next
  free number in the center's range.
- **RLS and COPY**: Postgres refuses `COPY` into a table with Row-Level Security. So we COPY
  into a temporary table and then `INSERT ... SELECT`, which RLS checks.
- **DSA MFI** has no standard LOINC code. It gets a local OMOP concept (id above 2 billion) and
  standard concept 0, and it is listed in the gaps report.

### Why these tools
- **PySpark + Delta Lake**: the same code scales from a laptop to a cluster. Delta adds safe
  overwrites of one tenant (`replaceWhere`), MERGE for insert-only bronze, and table history.
- **pandera**: declares what a clean table must look like (columns, types, allowed values) and
  checks it on Spark DataFrames.
- **OMOP `source_to_concept_map`**: the standard place for local mappings, so other OMOP tools
  understand them, and `make vocab-check` validates every row.
- **Databricks Free Edition**: shows the pipeline runs on a real lakehouse platform (Unity
  Catalog tables and volumes, serverless jobs) at no cost.

### Trade-offs
- Silver and gold are rebuilt per tenant on every run. That is simple and correct, but in
  production you would process only new data with incremental Delta MERGE. The insert-only
  bronze and the id map are already built for that.
- Conditions are mapped only when the center sent a code (49.6% coverage). Center B's text-only
  conditions wait for the note NLP step.
- Problem-list conditions have no date in the feeds, so we date them at the transplant. This is
  a documented assumption.
- An exact-duplicate filter cannot tell a re-sent row from two genuinely identical results. The
  report counts that case separately (1 row).

### Likely interview questions
1. **Why bronze, silver and gold instead of one job?** Bronze lets you replay everything after a
   bug fix without asking the hospital again. Silver shows exactly what was cleaned and why.
   Gold is a stable product for users. Each layer can be checked on its own.
2. **How do you make the pipeline idempotent?**
   - Bronze skips files it already has.
   - Silver and gold replace one tenant's rows at a time.
   - Ids come from a persistent id map.
   - Postgres is replaced per tenant in one transaction.

   Running it twice gives identical tables, and we test that.
3. **What do you do with bad data?** Bad rows go to quarantine with a reason and the run
   continues. If a whole table looks broken (schema change, duplicate ids, more than 5% bad rows),
   the run stops before loading anything. The report checks that exactly the injected bad rows
   were quarantined.
4. **How do you map "Prograf" to tacrolimus without fuzzy matching?** Prograf is an RxNorm Brand
   Name with a "Brand name of" relationship to the ingredient tacrolimus. Abbreviations like
   "tacro" go in a reviewed `source_to_concept_map`. Anything else gets concept 0 and goes to
   the review queue.
5. **How do you prove the pipeline is correct?** The synthetic generator records the true value
   of every lab and which rows it broke on purpose. The reconciliation script matches all
   240,945 lab results row by row in standard units, plus patients, deaths, medications,
   duplicates, quarantine and Postgres counts. All 12 checks pass.
