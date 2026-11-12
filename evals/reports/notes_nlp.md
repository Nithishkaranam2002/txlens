# Clinical-note NLP: evaluation

Generated 2026-11-11 by `make notes-report` (evals/src/txlens_evals/notes_nlp.py). Synthetic data only.

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
| rules-only | condition | 88.0% | 100.0% | 96.1% | 91.9% |
| rules-only | medication | 100.0% | 100.0% | 100.0% | 100.0% |
| rules-only | lab | 80.1% | 99.9% | 100.0% | 88.9% |
| rules-only | procedure | 97.1% | 97.1% | 100.0% | 98.6% |
| rules-only | **all** | 88.9% | 99.6% | 98.2% | 93.3% |
| medspaCy | condition | 88.0% | 100.0% | 96.1% | 91.9% |
| medspaCy | medication | 100.0% | 100.0% | 100.0% | 100.0% |
| medspaCy | lab | 80.1% | 99.9% | 100.0% | 88.9% |
| medspaCy | procedure | 97.1% | 97.1% | 100.0% | 98.6% |
| medspaCy | **all** | 88.9% | 99.6% | 98.2% | 93.3% |
| hybrid (+LLM) | condition | 88.4% | 100.0% | 100.0% | 93.9% |
| hybrid (+LLM) | medication | 100.0% | 100.0% | 100.0% | 100.0% |
| hybrid (+LLM) | lab | 80.1% | 99.9% | 100.0% | 88.9% |
| hybrid (+LLM) | procedure | 97.1% | 97.1% | 100.0% | 98.6% |
| hybrid (+LLM) | **all** | 89.1% | 99.6% | 100.0% | 94.2% |

### Attributes of matched mentions

| System | Matched facts | Negation | Temporality | Experiencer | Concept |
|---|---|---|---|---|---|
| rules-only | 195,083 | 79.1% | 69.3% | 94.3% | 100.0% |
| medspaCy | 195,083 | 100.0% | 99.8% | 100.0% | 100.0% |
| hybrid (+LLM) | 198,571 | 100.0% | 99.8% | 100.0% | 100.0% |

### Most frequent remaining errors (hybrid (+LLM))

| Error | Concept / category | Text | Count |
|---|---|---|---|
| false positive | procedure | `biopsy` | 726 |
| wrong temporality | graft_biopsy | `biopsy` | 97 |
| wrong temporality | tcmr | `tcmr` | 51 |
| wrong temporality | abmr | `abmr` | 35 |
| wrong temporality | tcmr | `acute t-cell mediated rejection` | 35 |
| wrong temporality | borderline_rejection | `borderline changes` | 30 |
| wrong temporality | abmr | `antibody-mediated rejection` | 30 |
| false positive | lab | `cr` | 28 |
| wrong temporality | borderline_rejection | `borderline rejection` | 25 |
| wrong temporality | mixed_rejection | `mixed rejection` | 23 |
| wrong temporality | mixed_rejection | `mixed tcmr/abmr` | 20 |
| wrong temporality | tcmr | `acute cellular rejection` | 14 |
| wrong temporality | tcmr | `acr` | 10 |
| wrong temporality | borderline_rejection | `mild acr` | 8 |
| wrong temporality | abmr | `amr` | 4 |

## Tuning notes (300 notes, seed 7: read while writing the rules)

300 notes, 4,918 ground-truth facts.

### Mentions found (precision, recall, F1)

| System | Category | Precision | Precision (tracked text) | Recall | F1 |
|---|---|---|---|---|---|
| rules-only | condition | 87.9% | 100.0% | 95.9% | 91.7% |
| rules-only | medication | 100.0% | 100.0% | 100.0% | 100.0% |
| rules-only | lab | 80.3% | 100.0% | 100.0% | 89.1% |
| rules-only | procedure | 98.1% | 98.1% | 100.0% | 99.0% |
| rules-only | **all** | 89.0% | 99.8% | 98.1% | 93.3% |
| medspaCy | condition | 87.9% | 100.0% | 95.9% | 91.7% |
| medspaCy | medication | 100.0% | 100.0% | 100.0% | 100.0% |
| medspaCy | lab | 80.3% | 100.0% | 100.0% | 89.1% |
| medspaCy | procedure | 98.1% | 98.1% | 100.0% | 99.0% |
| medspaCy | **all** | 89.0% | 99.8% | 98.1% | 93.3% |
| hybrid (+LLM) | condition | 88.3% | 100.0% | 100.0% | 93.8% |
| hybrid (+LLM) | medication | 100.0% | 100.0% | 100.0% | 100.0% |
| hybrid (+LLM) | lab | 80.3% | 100.0% | 100.0% | 89.1% |
| hybrid (+LLM) | procedure | 98.1% | 98.1% | 100.0% | 99.0% |
| hybrid (+LLM) | **all** | 89.2% | 99.8% | 100.0% | 94.3% |

### Attributes of matched mentions

| System | Matched facts | Negation | Temporality | Experiencer | Concept |
|---|---|---|---|---|---|
| rules-only | 4,827 | 78.9% | 69.0% | 94.6% | 100.0% |
| medspaCy | 4,827 | 100.0% | 99.8% | 100.0% | 100.0% |
| hybrid (+LLM) | 4,918 | 100.0% | 99.8% | 100.0% | 100.0% |

### Most frequent remaining errors (hybrid (+LLM))

| Error | Concept / category | Text | Count |
|---|---|---|---|
| false positive | procedure | `biopsy` | 12 |
| wrong temporality | graft_biopsy | `biopsy` | 2 |
| wrong temporality | mixed_rejection | `mixed rejection` | 1 |
| wrong temporality | tcmr | `acute t-cell mediated rejection` | 1 |
| wrong temporality | borderline_rejection | `mild acute cellular rejection` | 1 |
| wrong temporality | tcmr | `acr` | 1 |
| wrong temporality | mixed_rejection | `mixed tcmr/abmr` | 1 |
| wrong temporality | borderline_rejection | `borderline changes` | 1 |
| wrong temporality | abmr | `antibody-mediated rejection` | 1 |
| wrong temporality | abmr | `abmr` | 1 |

## LLM fallback (hybrid)

| | |
|---|---|
| Sentences sent (why) | 3,579 (unmatched_item 3,579) |
| Model calls | 15 paid, 3,564 answered from the cache, 0 failed |
| Tokens | 1,323,718 in, 364,984 out |
| Spent by this run | $0.0016 |
| Batch total (every run of this sample) / cap | $0.0016 / $3.00 |
| Without the cache, these calls cost | $0.3149 ($0.00003 per note) |
| Projected for all 12,505 notes | $0.31 (an upper bound: repeated sentences are answered from the cache) |
| Quotes not found in the sentence (dropped) | 0 |
| Budget reached | no |

## Speed

- rules-only: 10.4 ms per note
- medspaCy: 12.1 ms per note
- hybrid (+LLM): 15.9 ms per note

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
- **Held-out is not the same as unseen text.** The held-out notes were never read while
  writing rules, so they guard against fitting individual notes. They come from the same
  templates, though, so held-out and tuning scores are close; that closeness says nothing
  about real notes.
- **The abbreviation list and the condition-text map are curated by an AI assistant and are
  pending clinical review** (TXLENS_NOTE_ABBREV, TXLENS_CONDITION and TXLENS_COND_VALUE
  rows of SOURCE_TO_CONCEPT_MAP.csv). No clinician has checked them yet.
- **The ground truth comes from the same generator,** so there is no annotation noise and no
  disagreement between annotators. Real evaluation needs clinician-annotated notes.
- **"Graft tenderness" has no SNOMED concept** in our vocabulary. The LLM finds it, but it is
  stored with concept 0 and goes to review.
- **Concept accuracy** counts a mapping as correct when it is the expected concept or one of
  its descendants: "rejection" may be any transplanted-organ rejection.
- **The LLM sees one de-identified sentence at a time.** It never sees a whole note,
  identifiers or dates. Its findings must quote the sentence, and its concept comes from our
  vocabulary.
