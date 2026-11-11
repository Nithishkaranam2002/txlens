# ADR 0008: Identifiers outside OMOP; de-identified views for researchers and stewards

**Status:** accepted (2026-11-10)

## Context
Clinicians need to know who a patient is. Researchers must not: they get de-identified data
only (CLAUDE.md hard rule). Data stewards fix data quality and do not need identities either.
Before this step, `cdm.person` held the MRN (`person_source_value`) and the exact birth date.
Any query against OMOP, including the future text-to-SQL copilot, could therefore read
identifiers.

## Options compared

| | **Identity table + de-identification in the API** (chosen) | Identifiers in OMOP, filtered in the API | Separate de-identified copy of the database |
|---|---|---|---|
| Identifiers reachable by OMOP queries | No | Yes | No (in the copy) |
| Consistency between views | One source | One source | Copy can go stale |
| Work | Pipeline + API | API only | Extra pipeline + storage |

## Decision
- **Identifiers live only in `app.patient_identity`** (MRN, given name, family name, exact
  birth date). It has RLS like every clinical table and is loaded by the pipeline in the same
  transaction as `cdm`. `cdm.person` keeps only `year_of_birth`; `person_source_value`, month,
  day and birth datetime are empty. Only the clinician code path reads the identity table.
- **Pseudonyms**: `R-` + the first 16 hex characters of HMAC-SHA256(`DEID_HMAC_KEY`,
  "pid:tenant:person"). A plain hash of a small id space can be reversed by trying every id;
  without the key it cannot. The same patient always gets the same pseudonym, and rotating
  the key unlinks old exports. The API finds a patient by pseudonym in SQL with pgcrypto's
  `hmac()`; the key is a bind parameter, never stored.
- **Date shifting**: every date of one patient moves by the same HMAC-derived number of days
  (±1..182, never 0). Intervals such as "days since transplant" stay exact. Calendar dates do
  not.
- **Age bands**: 5-year bands at transplant, top-coded at 90+.
- **No free text**: de-identified roles get note dates only (no text, title or note id). No
  role gets note text through the API in this step.
- **Small-cell suppression**: for de-identified roles, any count from 1 to 10 (preview
  totals, every attrition step, cohort member counts, member-list totals) is shown as `"<11"`
  (`SMALL_CELL_THRESHOLD`, default 11). Zero is shown as 0.
- **Date-filter inference**: de-identified roles may filter transplant dates by whole
  calendar years only (start Jan 1, end Dec 31); finer ranges get 422. Otherwise a researcher
  could narrow a date range day by day, watch the count change, and recover true dates.
- **Role matrix**: clinician = identified (every view audited as `phi_view`); researcher =
  de-identified + cohorts; data_steward = de-identified patients and timeline, no cohorts;
  admin = no patient data.
- **404, not 403**, for ids outside the caller's center. RLS makes "other center" and "does
  not exist" look the same, so the API cannot confirm an id exists elsewhere.

## Consequences
- A test crawls every response a researcher or steward can get, in every center, and fails
  on any MRN, name, birth date or person id from the identity table. A clinician run proves
  the scanner works.
- Cohort rules are evaluated on true dates; researchers see shifted output. Whole-year
  granularity limits what the rules reveal.
- `cdm.note.note_text` still contains identifiers (the generated note header names the
  patient). The API never returns it. The Phase 2 NLP step must treat it as PHI.
- Re-identification risk is reduced, not zero: rare combinations (age band + organ + donor
  type + status) can still be distinctive. Formal k-anonymity checks are future work.
- Losing `DEID_HMAC_KEY` breaks the link to earlier pseudonyms. Leaking it lets someone
  recompute pseudonyms from person ids, so it is a secret like `JWT_SECRET`.
