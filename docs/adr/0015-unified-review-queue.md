# ADR 0015: One human review queue, with evidence shaped per role

**Status:** accepted (2026-11-12)

## Context
Steps 2.1 and 2.2 sent work to people but gave them no way to do it:
- 898 biopsy reports were `in_review`, including the 10% QC sample;
- 3,579 note mentions were in review;
- condition texts the pipeline could not map were stored only in a Delta table;
- 82 mapping rows were labelled "curated by AI assistant, pending clinical review".

The project rules say that the LLM never decides and that low-confidence results go to a
person. They also say data stewards see de-identified data only, and that every PHI view
is audited.

## Decision
- **One queue, read from where the items live.** The queue is built from the four sources
  (`app.document`, `app.note_mention`, `app.mapping_gap` and `vocab.source_to_concept_map`)
  instead of being copied into one queue table, so it can never fall out of sync. Row-Level
  Security limits a center to its own items.
- **Order:**
  1. rejection-related or high-severity items: a rejection diagnosis, ABMR or mixed, Banff
     II+ or ISHLT 2R+, rejection note mentions, and the new past-rejection review rule;
  2. the QC sample;
  3. everything else.

  Within each group, the oldest item comes first.
- **Decisions** go to the append-only `app.review_decision` table. Edit, reject and revert
  need a reason code, and "other" also needs a note. A database CHECK enforces this, as well
  as the API.
  - Biopsy decisions are sent to the waiting Temporal workflow (the `reason_code` field was
    added to the signal with a default, so older payloads still work).
  - Note mention decisions are applied at once. They are re-applied whenever the note NLP
    runs again, keyed by note and span.
- **AI-curated mapping rows are reviewed once, for every center.** They are one shared
  vocabulary, so `app.curated_mapping_history` has no tenant. It records the reviewer, the
  reviewer's center (checked against the session's tenant), the date and the reason.
  - The history is append-only: approve and correct add a version, and a revert adds an
    entry that undoes the latest version still in force.
  - Only clinicians decide on these rows.
  - `txlens-vocab export-curation` writes the "clinician-reviewed" label and the
    corrections back to the CSV, so the next pipeline run uses them for every center.
- **Evidence per role:**
  - Clinicians get the page image with a box per field, and the note sentence as written.
  - Data stewards never get a page. They get redacted lines and, for lines below the
    report's identity header, a cropped line image with identifier-like words painted
    black.
  - **The header refusal is geometric.** A crop overlapping the top 2.4 inches of page 1
    is refused even if OCR could not read the identifier there. A test checks the
    generator's real layout with Tesseract.
- **Feedback loop.** Every reviewed field becomes an `app.labelled_example` row: the model's
  value, the reviewer's value and de-identified evidence, with dates masked. Agreement is
  reported with the QC sample apart, because only QC items were chosen at random.
- **Metrics** (queue size, reviews per day, median time to review, agreement) are aggregates,
  so admins may see them too.

## Alternatives
- **A materialized `review_item` table** filled by triggers or by each producer. It would
  make paging and metrics simpler. But every producer would also need to keep it in sync,
  and a missed update hides an item. At this size (a few thousand items per center), reading
  the sources is fast enough.
- **One curated-mapping review per center.** Centers could disagree, and the shared CSV
  could not say which answer is right. The user chose one review for every center.
- **Text-only evidence for stewards** (the Step 2.1 behaviour). It is safer, but stewards
  cannot check OCR errors without seeing the scan. Crops limited by geometry plus word
  masking were chosen, with the residual risk written down below.
- **Keep `POST /documents/{id}/review` beside the queue route.** Two routes for one decision
  would drift apart, so the queue route replaced it. The frontend had never called the old
  one.

## Consequences
- Data stewards can fix OCR misreads without seeing who the patient is. The remaining risk
  is an identifier that sits below the header, which OCR misreads so badly that no masking
  rule recognises it. The header band, identifier-line refusal, date and number patterns
  and fuzzy name matching make this unlikely, not impossible. A real deployment should add
  a second, image-based PHI detector before showing crops.
- Agreement numbers mean something only once people review items. The dev database has no
  reviews yet, so the report says so instead of showing invented numbers.
- Queries scan the sources on each request. A center with tens of thousands of waiting items
  would need the materialized table above.
- The 3,579 waiting note mentions were all "graft tenderness" (no concept). SNOMED has no
  transplant-specific concept: only "Tenderness of kidney" (any kidney, Observation
  domain). Kidney notes therefore map the term to a TxLens local concept
  (GRAFT_TENDERNESS, AI-curated, pending clinical review). The note queue went from 3,579
  to 0, and the new mapping row waits in the queue for a clinician. The note NLP's version
  name now includes a vocabulary fingerprint, so a mapping change reprocesses every note.
