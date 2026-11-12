# ADR 0014: Clinical NLP for free-text conditions and clinic notes

**Status:** accepted (2026-11-11)

## Context
Two kinds of clinical text were unused:
- **Free-text FHIR conditions.** Center B sends conditions as text only, and center C codes
  about half of them. Only 49.6% of conditions had a concept, and the diabetes cohort found
  428 of 820 patients.
- **12,505 clinic notes.** No code read them, and `cdm.note.note_text` still held
  identifiers (the open gap in ADR 0008).

CLAUDE.md sets the boundaries:
- the LLM never decides;
- every output keeps its source;
- low-confidence results go to review;
- researchers see de-identified data only.

The owner pays for the API, so the brief was "deterministic first, the LLM only where
needed".

## Options compared

**Free-text conditions**

| | **Vocabulary matcher, no LLM** (chosen) | LLM picks the concept | Embeddings + nearest concept |
|---|---|---|---|
| Cost | none | per text | an index to build and host |
| Reproducible | yes | needs a cache | yes |
| Clinical qualifiers (type 1/2, acute/chronic, stage 3/4) | checked exactly by code | the model may drop them | similar vectors for "type 1" and "type 2" |
| Runs on Databricks | yes (plain Python on the driver) | needs network and keys | needs the index |

**Note NLP**

| | Rules only | **medspaCy + LLM fallback** (chosen) | LLM on every note |
|---|---|---|---|
| Negation, history, family | none (everything affirmed) | ConText rules | model |
| Cost for 12,505 notes | $0 | about $0.30 (projected from the 300-note sample) | dollars, and grows with every note |
| Evidence | exact span | exact span; LLM quotes are checked against the sentence | must be checked |
| PHI sent out | none | one de-identified sentence at a time | whole notes |

## Decision
- **Free-text conditions (pipeline, no LLM).** One matcher shared with the note NLP. The
  first tier that gives exactly one concept wins:
  1. the curated map (`TXLENS_CONDITION`);
  2. exact name or English synonym;
  3. normalized (same words, any order);
  4. fuzzy (difflib), accepted at 0.90 or above.

  Tiers 2-4 must pass a **qualifier guard**: type, acuity, side, grade, stage, class and
  numbers must match the concept's name exactly ("CKD" implies chronic). Anything that
  fails, ties or scores low keeps concept 0 and goes to the review list with its best
  suggestion. The row goes to the table of its concept's domain.
- **"History of" texts use the standard OMOP pattern.** `observation_concept_id` is the
  standard **History of event** (1340204, OMOP Extension; SNOMED "History of procedure" and
  "History of clinical finding" both map to it). `value_as_concept_id` is the transplant
  procedure (Transplant of kidney 4322471, Transplantation of heart 4137127). The value is
  a second curated row (`TXLENS_COND_VALUE`, same source text). Every target is a standard
  concept, and `make vocab-check` rejects anything else. It also checks that each value row
  has its observation row.
- **Curated mappings are labelled as such.** The `TXLENS_CONDITION`, `TXLENS_COND_VALUE` and
  `TXLENS_NOTE_ABBREV` rows were written by an AI assistant. Their description says
  "curated by AI assistant, pending clinical review", and the method is called "curated",
  never "reviewed". A test fails if a row loses the label.
- **De-identify before any NLP.** In gold, names become HMAC surrogates, MRNs `[MRN]`, and
  dates move by the same per-patient shift the API uses. The replaced words go to the
  clinician-only `app.note_phi_span`. The identified text never enters Postgres.
- **medspaCy rules:**
  - sections;
  - a target matcher built from the loaded vocabulary (guarded synonyms of the seed concepts,
    RxNorm brands, curated clinic abbreviations, organ-specific where the meaning differs:
    "TCMR" in a heart note is cardiac rejection);
  - ConText with transplant rules ("s/p", "h/o", "post-op course", "if").
- **The LLM only for sentences the rules cannot resolve.** Code decides when, with three
  triggers:
  - an ambiguous term;
  - a mention with contradicting modifiers;
  - a cue list ("Denies X, Y, Z") with an item no rule matched.

  The model sees one de-identified sentence, with dates masked again. Every finding must
  quote the sentence, or it is dropped. The concept comes from our vocabulary lookup of
  the quote, never from the model. LLM mentions get confidence 0.5, so they always go to
  review and never reach `condition_occurrence` on their own. It uses the same LiteLLM call,
  cache, atomic budget and Langfuse trace as biopsy extraction, with prompt version
  `notes-2026-11-11.1`.
- **OMOP.**
  - Every mention goes to `note_nlp`: offset, sentence, `term_exists`, `term_temporal`,
    and experiencer, section, method, confidence and status in `term_modifiers`.
  - A note adds one `condition_occurrence` per concept (type 32858 NLP, dated at the note),
    only from mentions that are affirmed, current, about the patient, with a standard
    concept and a confidence of at least 0.70.
  - TxLens local concepts (borderline rejection) stay in `note_nlp` as source concepts.
- **App tables are the source of truth, as for biopsies.**
  - `app.note_processed`, `app.note_mention` and `app.note_cdm_row`;
  - `app.load_note_nlp_results()` copies them into cdm, and the pipeline load calls it after
    every reload;
  - `app.note_cdm_row` is the condition-to-note link that OMOP v5.4 lacks.
- **Orchestration.** `NoteNlpBatchWorkflow` on Temporal (`txlens-notes process`) runs chunks
  of notes under one budget. A note whose text hash and NLP system are unchanged is
  skipped. Every saved note writes an audit row.
- **Display.** A note condition appears once per concept, at its first note, with "mentioned
  in N notes" and a "from note" badge:
  - clinicians see the sentence as written;
  - data stewards see the de-identified sentence;
  - researchers see no sentence.
- **Evaluation.** `make notes-report` scores against the generator's `note_facts.jsonl` and
  compares rules-only, medspaCy and hybrid. The 300 notes read while writing rules (seed 7)
  are reported separately; the held-out notes are the headline.

## Consequences
- **These numbers do not transfer to real notes.**
  - The notes come from a few templates with a closed list of 38 concepts.
  - The abbreviation list and three ConText rules were written knowing the generator.
  - Held-out notes guard against fitting individual notes, not against fitting the
    templates. Real notes would need clinician-annotated evaluation sets.
- **The curated rows are a clinical-safety debt.** Until a clinician reviews the 82 rows,
  they are assistant work. The review can use `evals/reports/condition_mapping_review.csv`
  and the vocab-check report.
- **Duplicates.** NLP conditions are a second source next to coded conditions. A patient can
  have hypertension from the problem list and from notes. Cohort rules count patients, not
  rows, so counts are unaffected. Row-level analyses should filter on
  `condition_type_concept_id`.
- **Review.** The review list (concept 0, ambiguous terms, LLM findings) is stored with a
  status; the UI to work through it is Step 2.3.
- **Real PHI.** As with biopsies, the de-identified sentence goes to the public OpenAI API
  only because the data is synthetic. Real notes need a provider under a BAA, or a local
  model, which is a LiteLLM configuration change.

## Update (2026-11-12, Step 2.3)
- **Past rejections were read as current.** HPI sentences like "Bx 3/22/24 showed ABMR,
  treated." had no history cue, so about 285 held-out rejection mentions became current
  conditions dated at a later visit. Two ConText rules found on the tuning notes only fixed
  it: ", treated" closes a past episode ("being treated" stays current), and "will arrange"
  / "will schedule" are plans. Held-out temporality went from 99.8% to 100.0%.
- **A safety net that does not trust the templates.** A rejection mention the rules still
  call current goes to review when its sentence has a date before the date of service or a
  treatment cue (`review_reason`).
- **The hybrid nlp_system name now includes the rules version**
  (`txlens-medspacy-2+llm-<prompt>`), so a rules change makes the batch redo every note.
  Before, only a prompt change did.
- Reviewer decisions on mentions are re-applied when a note is processed again (ADR 0015).
