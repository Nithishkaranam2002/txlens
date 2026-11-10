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
