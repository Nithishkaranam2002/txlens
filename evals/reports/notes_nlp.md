# Clinical-note NLP: evaluation

Generated 2026-11-12 by `make notes-report` (evals/src/txlens_evals/notes_nlp.py). Synthetic data only.

| Run | |
|---|---|
| Sample | all notes |
| Model | openai/gpt-6-luna |
| Prompt | notes-2026-11-11.1 |
| Batch | eval-notes-full |
| Notes | 12,505 |
| Ground-truth facts | 203,489 |

## Held-out notes (never read while writing the rules)

12,205 notes, 198,571 ground-truth facts.

### Mentions found (precision, recall, F1)

| System | Category | Precision | Precision (tracked text) | Recall | F1 |
|---|---|---|---|---|---|
| rules-only | condition | 88.4% | 100.0% | 100.0% | 93.9% |
| rules-only | medication | 100.0% | 100.0% | 100.0% | 100.0% |
| rules-only | lab | 80.1% | 99.9% | 100.0% | 88.9% |
| rules-only | procedure | 97.1% | 97.1% | 100.0% | 98.6% |
| rules-only | **all** | 89.1% | 99.6% | 100.0% | 94.2% |
| medspaCy | condition | 88.4% | 100.0% | 100.0% | 93.9% |
| medspaCy | medication | 100.0% | 100.0% | 100.0% | 100.0% |
| medspaCy | lab | 80.1% | 99.9% | 100.0% | 88.9% |
| medspaCy | procedure | 97.1% | 97.1% | 100.0% | 98.6% |
| medspaCy | **all** | 89.1% | 99.6% | 100.0% | 94.2% |
| hybrid (+LLM) | condition | 88.4% | 100.0% | 100.0% | 93.9% |
| hybrid (+LLM) | medication | 100.0% | 100.0% | 100.0% | 100.0% |
| hybrid (+LLM) | lab | 80.1% | 99.9% | 100.0% | 88.9% |
| hybrid (+LLM) | procedure | 97.1% | 97.1% | 100.0% | 98.6% |
| hybrid (+LLM) | **all** | 89.1% | 99.6% | 100.0% | 94.2% |

### Attributes of matched mentions

| System | Matched facts | Negation | Temporality | Experiencer | Concept |
|---|---|---|---|---|---|
| rules-only | 198,571 | 77.8% | 69.8% | 94.4% | 100.0% |
| medspaCy | 198,571 | 100.0% | 100.0% | 100.0% | 100.0% |
| hybrid (+LLM) | 198,571 | 100.0% | 100.0% | 100.0% | 100.0% |

### Rejections called current but dated earlier or treated (sent to review)

| System | Sent to review | Truly historical | Truly current |
|---|---|---|---|
| rules-only | 0 | 0 | 0 |
| medspaCy | 0 | 0 | 0 |
| hybrid (+LLM) | 0 | 0 | 0 |

### Most frequent remaining errors (hybrid (+LLM))

| Error | Concept / category | Text | Count |
|---|---|---|---|
| false positive | procedure | `biopsy` | 726 |
| false positive | lab | `cr` | 28 |

## Tuning notes (300 notes, seed 7: read while writing the rules)

300 notes, 4,918 ground-truth facts.

### Mentions found (precision, recall, F1)

| System | Category | Precision | Precision (tracked text) | Recall | F1 |
|---|---|---|---|---|---|
| rules-only | condition | 88.3% | 100.0% | 100.0% | 93.8% |
| rules-only | medication | 100.0% | 100.0% | 100.0% | 100.0% |
| rules-only | lab | 80.3% | 100.0% | 100.0% | 89.1% |
| rules-only | procedure | 98.1% | 98.1% | 100.0% | 99.0% |
| rules-only | **all** | 89.2% | 99.8% | 100.0% | 94.3% |
| medspaCy | condition | 88.3% | 100.0% | 100.0% | 93.8% |
| medspaCy | medication | 100.0% | 100.0% | 100.0% | 100.0% |
| medspaCy | lab | 80.3% | 100.0% | 100.0% | 89.1% |
| medspaCy | procedure | 98.1% | 98.1% | 100.0% | 99.0% |
| medspaCy | **all** | 89.2% | 99.8% | 100.0% | 94.3% |
| hybrid (+LLM) | condition | 88.3% | 100.0% | 100.0% | 93.8% |
| hybrid (+LLM) | medication | 100.0% | 100.0% | 100.0% | 100.0% |
| hybrid (+LLM) | lab | 80.3% | 100.0% | 100.0% | 89.1% |
| hybrid (+LLM) | procedure | 98.1% | 98.1% | 100.0% | 99.0% |
| hybrid (+LLM) | **all** | 89.2% | 99.8% | 100.0% | 94.3% |

### Attributes of matched mentions

| System | Matched facts | Negation | Temporality | Experiencer | Concept |
|---|---|---|---|---|---|
| rules-only | 4,918 | 77.4% | 69.6% | 94.7% | 100.0% |
| medspaCy | 4,918 | 100.0% | 100.0% | 100.0% | 100.0% |
| hybrid (+LLM) | 4,918 | 100.0% | 100.0% | 100.0% | 100.0% |

### Rejections called current but dated earlier or treated (sent to review)

| System | Sent to review | Truly historical | Truly current |
|---|---|---|---|
| rules-only | 0 | 0 | 0 |
| medspaCy | 0 | 0 | 0 |
| hybrid (+LLM) | 0 | 0 | 0 |

### Most frequent remaining errors (hybrid (+LLM))

| Error | Concept / category | Text | Count |
|---|---|---|---|
| false positive | procedure | `biopsy` | 12 |

## LLM fallback (hybrid)

| | |
|---|---|
| Sentences sent (why) | 0 (none) |
| Model calls | 0 paid, 0 answered from the cache, 0 failed |
| Tokens | 0 in, 0 out |
| Spent by this run | $0.0000 |
| Batch total (every run of this sample) / cap | $0.0016 / $3.00 |
| Without the cache, these calls cost | $0.0000 ($0.00000 per note) |
| Projected for all 12,505 notes | $0.00 (an upper bound: repeated sentences are answered from the cache) |
| Quotes not found in the sentence (dropped) | 0 |
| Budget reached | no |

## Speed

- rules-only: 10.2 ms per note
- medspaCy: 11.7 ms per note
- hybrid (+LLM): 12.1 ms per note

## Honest limits

- **These notes are much easier than real ones.** One generator writes every note from a
  handful of templates (HPI, PMH, FHx, Meds, Labs, A/P), with a closed list of 38 concepts and
  a few surface forms each. Real notes have typos, copy-forward text, tables, unusual
  abbreviations, long problem lists and phrasing no rule has seen. Expect much lower numbers
  on real text; these numbers show the pipeline works, not how well it generalizes.
- **The rules were written knowing the generator.** The abbreviation list (TXLENS_NOTE_ABBREV)
  and three rules were added after reading errors on the 300 tuning notes (seed 7). The three
  rules are: PMH lists active conditions; the transplant indication and DGF are historical;
  "post-op course" is historical. They are clinically motivated, but they are tuned to this
  generator. Before them, medspaCy scored 88.0% on temporality and 94.0% on concepts.
  Step 2.3 added two more after reading the tuning errors: ", treated" closes a past episode,
  and "will arrange" is a plan (hypothetical). Before them, held-out temporality was 99.8%,
  with ~285 past rejections read as current.
- **A safety net, not a guess.** A rejection the rules still call current, in a sentence with
  a date before the date of service or a treatment cue, goes to the review queue instead of
  into cohorts. It does not depend on the generator's wording.
- **Held-out is not the same as unseen text.** The held-out notes were never read while
  writing rules, so they guard against fitting individual notes. They come from the same
  templates, though, so held-out and tuning scores are close; that closeness says nothing
  about real notes.
- **The abbreviation list and the condition-text map are curated by an AI assistant and are
  pending clinical review** (TXLENS_NOTE_ABBREV, TXLENS_CONDITION and TXLENS_COND_VALUE
  rows of SOURCE_TO_CONCEPT_MAP.csv). No clinician has checked them yet.
- **The ground truth comes from the same generator,** so there is no annotation noise and no
  disagreement between annotators. Real evaluation needs clinician-annotated notes.
- **"Graft tenderness" has no standard concept.** SNOMED has only "Tenderness of kidney" (any
  kidney, Observation domain). Since Step 2.3 the term maps to a TxLens local concept
  (GRAFT_TENDERNESS, curated by AI assistant, pending clinical review); before that the LLM
  found it with concept 0 and every mention went to review.
- **Concept accuracy** counts a mapping as correct when it is the expected concept or one of
  its descendants: "rejection" may be any transplanted-organ rejection.
- **The LLM sees one de-identified sentence at a time.** It never sees a whole note,
  identifiers or dates. Its findings must quote the sentence, and its concept comes from our
  vocabulary.
