# ADR 0013: Biopsy report extraction (OCR, evidenced LLM extraction, review, OMOP)

**Status:** accepted (2026-11-11)

## Context
Biopsy results (diagnosis, Banff scores, ISHLT grades) exist only in scanned pathology PDFs.
The timeline showed "awaiting document extraction". We need structured, reviewable OMOP data
from these scans. CLAUDE.md sets the boundaries:
- the LLM extracts, it never decides;
- every value keeps a link to its source;
- low-confidence results go to a human;
- synthetic data only.

The project owner pays for the API, so cost must be capped and visible.

## Options compared

**OCR**

| | **Tesseract, Docling opt-in** (chosen) | Docling by default | Cloud OCR |
|---|---|---|---|
| Key tokens read exactly (30 kidney reports) | 60.4% | 61.7% | not tried (sends scans out) |
| Time per page | 0.7 s | 1.2 s, plus model loading | network |
| Confidence per word | yes | page-level score only | yes |
| Install size | system package | PyTorch + layout models (~1–2 GB) | none |

**Structured output**

| | **Structured output (`response_format`)** (chosen) | Tool calls | Free text + regex |
|---|---|---|---|
| Schema enforced | by the provider and by Pydantic | by Pydantic | no |
| Works with LiteLLM 1.106.5 | yes | no: it imports the optional MCP package for any request with tools | yes |

**Confidence**

| | **OCR confidence × agreement of two passes** (chosen) | Model's own confidence | Log-probabilities |
|---|---|---|---|
| Measurable, reproducible | yes | no (self-reported) | provider-specific |

## Decision
- **Pipeline:** one Temporal workflow per PDF, id `biopsy-<tenant>-<sha256>`. The steps:
  1. register by file hash;
  2. OCR and link to the biopsy;
  3. two LLM passes in parallel;
  4. assess;
  5. load into OMOP, or wait for the `submit_review` signal (approve / edit / reject) until
     `REVIEW_TIMEOUT_DAYS`, after which the document expires and nothing is loaded.

  A batch workflow starts documents a few at a time. Each document reports back once
  assessed, so a batch never waits for people.
- **Evidence:** every field is a value plus the line number and exact quote. A value
  survives only if three things hold:
  - the quote matches OCR words on a line that was sent to the model;
  - the value can be read back from the quote (look-alike characters tolerated only inside
    score and grade tokens);
  - the bounding box and confidence come from our OCR, never from the model.
- **Rules in code, not in the model:**
  - Banff consistency between diagnosis, grade and scores; BK nephropathy can mimic TCMR;
  - biopsy date within 7 days of the procedure;
  - heart diagnosis derived from the ISHLT grades.

  No rule fires on any correct report from the generator (tested).
- **Routing:** field confidence = OCR confidence of the evidence words × (1 if both passes
  agree, else 0). Document confidence is the weakest key field. A document goes to review
  if its confidence is below 0.85, or if any evidence check, validation rule or OMOP mapping
  fails.
- **Patient identifiers never reach the model:**
  - the header is parsed locally for the MRN and procedure date that link the report;
  - identifier lines are dropped;
  - other dates and long numbers are masked;
  - the linked patient's name and MRN are scrubbed.

  Langfuse receives only this text, with the file hash and prompt version as metadata.
- **Prompt injection:** the report is framed as data inside a `<document>` block. The model
  has no tools, every value must be quoted, and the rules run in Python. An injected
  instruction can at worst produce a value that fails these checks.
- **OMOP:**
  - TCMR maps to SNOMED "acute rejection of renal transplant, grade I/II/III"; ABMR, heart
    ACR, BK nephropathy, ATN and CMV to standard SNOMED.
  - Borderline and every score or grade use TxLens local concepts (`concept_id` 0, local
    `source_concept_id`), with type concept 32858 (NLP).
  - Measurements point at their biopsy (`measurement_event_id`).
  - `make vocab-check` verifies every concept by code (standard, domain, not deprecated).
- **Source of truth:** app tables (`app.document`, `app.extracted_field`,
  `app.document_cdm_row`) hold the results with their provenance. `app.load_biopsy_results()`
  copies them into `cdm`, and the pipeline loader calls it after every reload, because a
  reload replaces all cdm rows of a tenant.
- **Cost:**
  - Default model: `openai/gpt-6-luna` ($0.10 / $0.50 per million input / output tokens;
    the cheapest current OpenAI model). `ollama/llama3.1:8b` works through the same LiteLLM
    call at no cost.
  - A hard cap per batch (`LLM_BUDGET_USD`, default $3): each call reserves its worst-case
    cost with one atomic UPDATE before it runs, so parallel workers cannot overshoot.
  - Answers are cached by file hash, prompt version, model, pass and exact text.
  - Rate limits are retried with backoff by LiteLLM, then by Temporal (2 s to 60 s).
- **Access:**
  - clinicians see full evidence and page images;
  - data stewards see only redacted evidence lines;
  - researchers see the result but no document text.

  The de-identification scan covers these responses.

## Consequences
- **Real patient data would need a different deployment.** These reports are synthetic.
  Real PHI must never be sent to the public OpenAI API under these settings. It would need
  either Azure OpenAI (or another provider) under a Business Associate Agreement, with data
  retention and abuse monitoring configured, or a local model (`ollama/...`) inside the
  hospital network. The LiteLLM interface makes either a configuration change; the
  minimization layer stays in place regardless.
- OCR, not the LLM, causes most errors on these simulated scans. A dropped character (a
  "t2" read as "2.") is rejected by the evidence check and sends the document to review,
  rather than being guessed.
- The two passes use the same model with different prompts. Their agreement is a useful
  signal, but not independent evidence. `LLM_MODEL_PASS_B` can name a second model for
  stronger independence, at about twice the cost.
- Docling stays available (`OCR_ENGINE=docling`, dependency group `docling`) for reports with
  real layout (tables, columns). It is not installed in CI.
- Six transitive packages of LiteLLM and Instructor, and fifteen of the Docling group, are
  capped below releases that were days old. Every new package in the lock is at least two
  weeks old. The caps should be lifted once those releases have aged.
- Step 2.3 builds the review UI on top of the review and evidence endpoints.
