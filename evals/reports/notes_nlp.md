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

## Four systems on a held-out sample (300 notes, seed 11)

300 notes, 4,912 ground-truth facts.

### Headline (95% bootstrap intervals, notes resampled 1,000 times)

| System | F1 (mentions) | Negation | Temporality | Concept | Sent to review | LLM cost / note | Latency / note |
|---|---|---|---|---|---|---|---|
| rules-only | 94.3% (94.2%-94.4%) | 77.8% (77.1%-78.4%) | 69.6% (69.1%-70.0%) | 100.0% (100.0%-100.0%) | 0.0% (0.0%-0.0%) | $0.00000 | 10 ms |
| medspaCy | 94.3% (94.2%-94.4%) | 100.0% (100.0%-100.0%) | 100.0% (100.0%-100.0%) | 100.0% (100.0%-100.0%) | 0.0% (0.0%-0.0%) | $0.00000 | 12 ms |
| hybrid (+LLM) | 94.3% (94.2%-94.4%) | 100.0% (100.0%-100.0%) | 100.0% (100.0%-100.0%) | 100.0% (100.0%-100.0%) | 0.0% (0.0%-0.0%) | $0.00000 | 12 ms |
| LLM only | 84.0% (83.7%-84.2%) | 100.0% (100.0%-100.0%) | 85.3% (84.1%-86.3%) | 71.5% (70.1%-73.0%) | 100.0% (100.0%-100.0%) | $0.00163 | 41.8 s |

Accuracy of negation, temporality and concept is over the matched facts. Sent to review: mentions the pipeline would send to a person (no concept, ambiguous, or confidence below 0.70: every LLM finding). Cost is without the cache; latency is measured, plus the median paid-call time for each call answered from the cache.

### F1 per concept

| Concept | rules-only F1 | medspaCy F1 | hybrid (+LLM) F1 | LLM only F1 |
|---|---|---|---|---|
| abmr | 100.0% | 100.0% | 100.0% | 100.0% |
| borderline_rejection | 100.0% | 100.0% | 100.0% | 100.0% |
| cmv_infection | 100.0% | 100.0% | 100.0% | 100.0% |
| coronary_artery_disease | 100.0% | 100.0% | 100.0% | 100.0% |
| creatinine | 100.0% | 100.0% | 100.0% | 100.0% |
| dd_cfdna | 54.1% | 54.1% | 54.1% | 54.1% |
| dgf | 100.0% | 100.0% | 100.0% | 100.0% |
| diabetes | 100.0% | 100.0% | 100.0% | 99.3% |
| dsa | 100.0% | 100.0% | 100.0% | 100.0% |
| dsa_mfi | 100.0% | 100.0% | 100.0% | 100.0% |
| dyspnea | 100.0% | 100.0% | 100.0% | 100.0% |
| dysuria | 100.0% | 100.0% | 100.0% | 100.0% |
| edema | 100.0% | 100.0% | 100.0% | 100.0% |
| egfr | 100.0% | 100.0% | 100.0% | 100.0% |
| esrd | 100.0% | 100.0% | 100.0% | 100.0% |
| fever | 100.0% | 100.0% | 100.0% | 100.0% |
| graft_biopsy | 97.7% | 97.7% | 97.7% | 99.3% |
| graft_tenderness | 100.0% | 100.0% | 100.0% | 100.0% |
| heart_failure | 100.0% | 100.0% | 100.0% | 100.0% |
| heart_transplant | 100.0% | 100.0% | 100.0% | 100.0% |
| hypertension | 100.0% | 100.0% | 100.0% | 97.7% |
| immune globulin | 100.0% | 100.0% | 100.0% | 100.0% |
| kidney_transplant | 100.0% | 100.0% | 100.0% | 100.0% |
| methylprednisolone | 100.0% | 100.0% | 100.0% | 100.0% |
| mixed_rejection | 100.0% | 100.0% | 100.0% | 100.0% |
| mycophenolate mofetil | 100.0% | 100.0% | 100.0% | 100.0% |
| other | 0.0% | 0.0% | 0.0% | 0.0% |
| palpitations | 100.0% | 100.0% | 100.0% | 100.0% |
| polycystic_kidney_disease | 42.9% | 42.9% | 42.9% | 42.9% |
| prednisone | 100.0% | 100.0% | 100.0% | 100.0% |
| prior_transplant | 100.0% | 100.0% | 100.0% | 96.6% |
| rejection | 100.0% | 100.0% | 100.0% | 99.9% |
| tacrolimus | 100.0% | 100.0% | 100.0% | 96.9% |
| tacrolimus_trough | 100.0% | 100.0% | 100.0% | 96.5% |
| tcmr | 100.0% | 100.0% | 100.0% | 100.0% |

### Mentions found (precision, recall, F1)

| System | Category | Precision | Precision (tracked text) | Recall | F1 |
|---|---|---|---|---|---|
| rules-only | condition | 88.5% | 100.0% | 100.0% | 93.9% |
| rules-only | medication | 100.0% | 100.0% | 100.0% | 100.0% |
| rules-only | lab | 79.9% | 100.0% | 100.0% | 88.8% |
| rules-only | procedure | 97.8% | 97.8% | 100.0% | 98.9% |
| rules-only | **all** | 89.2% | 99.7% | 100.0% | 94.3% |
| medspaCy | condition | 88.5% | 100.0% | 100.0% | 93.9% |
| medspaCy | medication | 100.0% | 100.0% | 100.0% | 100.0% |
| medspaCy | lab | 79.9% | 100.0% | 100.0% | 88.8% |
| medspaCy | procedure | 97.8% | 97.8% | 100.0% | 98.9% |
| medspaCy | **all** | 89.2% | 99.7% | 100.0% | 94.3% |
| hybrid (+LLM) | condition | 88.5% | 100.0% | 100.0% | 93.9% |
| hybrid (+LLM) | medication | 100.0% | 100.0% | 100.0% | 100.0% |
| hybrid (+LLM) | lab | 79.9% | 100.0% | 100.0% | 88.8% |
| hybrid (+LLM) | procedure | 97.8% | 97.8% | 100.0% | 98.9% |
| hybrid (+LLM) | **all** | 89.2% | 99.7% | 100.0% | 94.3% |
| LLM only | condition | 73.4% | 81.2% | 99.3% | 84.4% |
| LLM only | medication | 74.0% | 74.0% | 100.0% | 85.1% |
| LLM only | lab | 65.9% | 79.2% | 98.3% | 78.9% |
| LLM only | procedure | 84.6% | 84.6% | 99.7% | 91.5% |
| LLM only | **all** | 72.8% | 79.7% | 99.2% | 84.0% |

### Attributes of matched mentions

| System | Matched facts | Negation | Temporality | Experiencer | Concept |
|---|---|---|---|---|---|
| rules-only | 4,912 | 77.8% | 69.6% | 94.2% | 100.0% |
| medspaCy | 4,912 | 100.0% | 100.0% | 100.0% | 100.0% |
| hybrid (+LLM) | 4,912 | 100.0% | 100.0% | 100.0% | 100.0% |
| LLM only | 4,875 | 100.0% | 85.3% | 100.0% | 71.5% |

### Rejections called current but dated earlier or treated (sent to review)

| System | Sent to review | Truly historical | Truly current |
|---|---|---|---|
| rules-only | 0 | 0 | 0 |
| medspaCy | 0 | 0 | 0 |
| hybrid (+LLM) | 0 | 0 | 0 |
| LLM only | 0 | 0 | 0 |

### Most frequent remaining errors (LLM only)

| Error | Concept / category | Text | Count |
|---|---|---|---|
| false positive | lab | `labs` | 293 |
| false positive | medication | `immunosuppression` | 293 |
| wrong concept | graft_biopsy | `biopsy` | 292 |
| false positive | condition | `graft function` | 290 |
| false positive | condition | `immediate graft function` | 214 |
| wrong temporality (current→historical) | hypertension | `htn` | 111 |
| wrong temporality (historical→current) | dgf | `delayed graft function` | 109 |
| wrong temporality (current→historical) | hypertension | `hypertension` | 93 |
| wrong concept | egfr | `gfr` | 75 |
| wrong concept | egfr | `egfr` | 73 |
| wrong concept | mycophenolate mofetil | `mmf` | 66 |
| wrong concept | prednisone | `pred` | 66 |
| wrong concept | prednisone | `prednisone` | 64 |
| wrong temporality (historical→current) | dgf | `dgf` | 62 |
| wrong temporality (current→historical) | diabetes | `type 2 diabetes` | 57 |

## Held-out notes (never read while writing the rules)

12,205 notes, 198,571 ground-truth facts.

### Headline (95% bootstrap intervals, notes resampled 1,000 times)

| System | F1 (mentions) | Negation | Temporality | Concept | Sent to review | LLM cost / note | Latency / note |
|---|---|---|---|---|---|---|---|
| rules-only | 94.2% (94.2%-94.2%) | 77.8% (77.7%-77.9%) | 69.8% (69.7%-69.9%) | 100.0% (100.0%-100.0%) | 0.0% (0.0%-0.0%) | $0.00000 | 10 ms |
| medspaCy | 94.2% (94.2%-94.2%) | 100.0% (100.0%-100.0%) | 100.0% (100.0%-100.0%) | 100.0% (100.0%-100.0%) | 0.0% (0.0%-0.0%) | $0.00000 | 12 ms |
| hybrid (+LLM) | 94.2% (94.2%-94.2%) | 100.0% (100.0%-100.0%) | 100.0% (100.0%-100.0%) | 100.0% (100.0%-100.0%) | 0.0% (0.0%-0.0%) | $0.00000 | 12 ms |

Accuracy of negation, temporality and concept is over the matched facts. Sent to review: mentions the pipeline would send to a person (no concept, ambiguous, or confidence below 0.70: every LLM finding). Cost is without the cache; latency is measured, plus the median paid-call time for each call answered from the cache.

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

### Headline (95% bootstrap intervals, notes resampled 1,000 times)

| System | F1 (mentions) | Negation | Temporality | Concept | Sent to review | LLM cost / note | Latency / note |
|---|---|---|---|---|---|---|---|
| rules-only | 94.3% (94.2%-94.4%) | 77.4% (76.8%-78.0%) | 69.6% (69.1%-70.0%) | 100.0% (100.0%-100.0%) | 0.0% (0.0%-0.0%) | $0.00000 | 10 ms |
| medspaCy | 94.3% (94.2%-94.4%) | 100.0% (100.0%-100.0%) | 100.0% (100.0%-100.0%) | 100.0% (100.0%-100.0%) | 0.0% (0.0%-0.0%) | $0.00000 | 12 ms |
| hybrid (+LLM) | 94.3% (94.2%-94.4%) | 100.0% (100.0%-100.0%) | 100.0% (100.0%-100.0%) | 100.0% (100.0%-100.0%) | 0.0% (0.0%-0.0%) | $0.00000 | 12 ms |

Accuracy of negation, temporality and concept is over the matched facts. Sent to review: mentions the pipeline would send to a person (no concept, ambiguous, or confidence below 0.70: every LLM finding). Cost is without the cache; latency is measured, plus the median paid-call time for each call answered from the cache.

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

LLM only, 300 held-out notes:

| | |
|---|---|
| Sentences sent (why) | 5,632 (every_sentence 5,632) |
| Model calls | 0 paid, 5,632 answered from the cache, 0 failed |
| Tokens | 2,086,981 in, 561,619 out |
| Spent by this run | $0.0000 |
| Batch total (every run of this sample) / cap | $0.1835 / $1.00 |
| Without the cache, these calls cost | $0.4895 ($0.00163 per note) |
| Projected for all 12,505 notes | $20.40 (an upper bound: repeated sentences are answered from the cache) |
| Quotes not found in the sentence (dropped) | 0 |
| Budget reached | no |

## Speed

- rules-only: 10.4 ms per note
- medspaCy: 11.7 ms per note
- hybrid (+LLM): 11.9 ms per note

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
