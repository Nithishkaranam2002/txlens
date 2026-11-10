# omop/: OMOP CDM v5.4 tables and vocabularies

The `txlens-omop` package holds:

| | |
|---|---|
| `src/txlens_omop/sql/v5.4.3/` | Official OHDSI Postgres DDL, pinned and unedited (see `SOURCE.md`) |
| `ddl.py` | Renders that DDL into our layout (`cdm` + `vocab` schemas); used by the Alembic migrations in `backend/` |
| `athena.py`, `loader.py` | Stream an Athena download (zip or folder) into `vocab` with `COPY` |
| `subset.py` | Extract a small vocabulary subset around the codes we use |
| `vocab.py` | Lookups: code → concept → standard concept, and descendants |
| `codes.py`, `check.py` | Inventory of every code we use, and the check against the vocabulary |
| `mappings.py`, `mappings/` | Reviewed `source_to_concept_map` rows (lab names and local codes, drug abbreviations, uncoded transplant texts) and the TxLens local concept for DSA MFI; loaded with the vocabulary and copied into the subset |
| `tests/fixtures/athena_fake/` | **TEST ONLY** fake vocabulary (fake codes, ids ≥ 2,100,000,000) |

Design decisions: `docs/adr/0004-multi-tenancy-rls.md` and `docs/adr/0005-omop-ddl-and-vocabulary.md`.

## Licensing: why the vocabulary is not in the repo

Athena vocabularies come under their owners' licences (for example SNOMED CT, and CPT4 from the
AMA via UMLS). We may use them, but **we must not redistribute them**. So:

- `omop/athena/` (your download) and `omop/vocab_subset/` (our extract) are **git-ignored and
  docker-ignored**. Never commit, upload or share their contents.
- Tests use only the fake fixture in `tests/fixtures/athena_fake/`.
- Each developer downloads their own copy from Athena.

## Get the vocabulary (once)

1. Sign in at <https://athena.ohdsi.org> and open **Download**.
2. Select at least: **SNOMED, LOINC, RxNorm, RxNorm Extension, ICD10CM, UCUM**. The small
   vocabularies Athena adds by default (Gender, Race, Visit, Type Concept, Domain, ...) are
   used too. CPT4 is not needed (we exclude it).
3. Choose CDM version **5.x**, name the bundle and download it when the email arrives.
4. Put the zip, unchanged, in `omop/athena/`. Do not unzip it: the loader reads it directly.

## Load it

```bash
make up            # Postgres
make db-migrate    # create schemas, tables, RLS, indexes
make vocab-load    # stream omop/athena/*.zip into the vocab schema (~3 min)
make vocab-check   # check every code TxLens uses (exit code 1 if any problem)
```

`make vocab-load` replaces the whole vocabulary in one transaction: if it fails, the previous
vocabulary stays. A missing optional vocabulary (for example UCUM) only prints a warning;
missing SNOMED, LOINC or RxNorm stops the load.

Measured on Athena release v20260829: 3,989,352 concepts, 68.6 million rows in total,
**7.1 GB** in Postgres (`concept_relationship` 3.9 GB, `concept_ancestor` 1.7 GB, `concept`
1.2 GB), 2 min 48 s, ~530 MB of memory.

### Small subset for quick local work

```bash
make vocab-subset                               # writes omop/vocab_subset/ (git-ignored)
make vocab-load VOCAB_SOURCE=omop/vocab_subset  # load it instead of the full download
```

The subset keeps our codes, what they "Map to", all their ancestors and descendants, and the
small support vocabularies.

## Code check

`make vocab-check` reads the codes straight from `data_generator` (LOINC labs, RxNorm drugs,
SNOMED conditions and procedures) plus planned ICD-10-CM source codes, and checks that each one
exists, has the expected domain, and is standard or "Maps to" a standard concept. It prints
the vocabulary's own name next to our label, because a code can exist and mean something else.
Last run (v20260829): 23 of 23 OK.

## Upgrading the CDM DDL

Add a new folder `sql/<version>/` with the new upstream files, `SHA256SUMS` and `SOURCE.md`,
then write a new Alembic migration that renders that version. Never edit pinned files.
