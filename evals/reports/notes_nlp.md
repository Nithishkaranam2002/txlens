# Clinical-note NLP: evaluation

Generated 2026-11-11 by `make notes-report` (evals/src/txlens_evals/notes_nlp.py). Synthetic data only.

| Run | |
|---|---|
| Sample | 300 notes, seed 7 |
| Model | openai/gpt-6-luna |
| Prompt | notes-2026-11-11.1 |
| Batch | eval-notes-300-7 |
| Notes | 300 |
| Ground-truth facts | 4,918 |

## Mentions found (precision, recall, F1)

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

## Attributes of matched mentions

| System | Matched facts | Negation | Temporality | Experiencer | Concept |
|---|---|---|---|---|---|
| rules-only | 4,827 | 78.9% | 69.0% | 94.6% | 100.0% |
| medspaCy | 4,827 | 100.0% | 99.8% | 100.0% | 100.0% |
| hybrid (+LLM) | 4,918 | 100.0% | 99.8% | 100.0% | 100.0% |

## Most frequent remaining errors (hybrid (+LLM))

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
| Sentences sent (why) | 91 (unmatched_item 91) |
| Model calls | 0 paid, 91 answered from the cache, 0 failed |
| Tokens | 33,685 in, 9,644 out |
| Spent by this run | $0.0000 |
| Batch total (every run of this sample) / cap | $0.0072 / $1.00 |
| Without the cache, these calls cost | $0.0082 ($0.00003 per note) |
| Projected for all 12,505 notes | $0.34 (an upper bound: repeated sentences are answered from the cache) |
| Quotes not found in the sentence (dropped) | 0 |
| Budget reached | no |

## Speed

- rules-only: 10.4 ms per note
- medspaCy: 11.4 ms per note
- hybrid (+LLM): 12.3 ms per note

## Honest limits

- **These notes are much easier than real ones.** One generator writes every note from a
  handful of templates (HPI, PMH, FHx, Meds, Labs, A/P), with a closed list of 38 concepts and
  a few surface forms each. Real notes have typos, copy-forward text, tables, unusual
  abbreviations, long problem lists and phrasing no rule has seen. Expect much lower numbers
  on real text; these numbers show the pipeline works, not how well it generalizes.
- **The rules were written knowing the generator.** The abbreviation list (TXLENS_NOTE_ABBREV)
  and three rules were added after reading errors on this sample. The three rules are: PMH
  lists active conditions; the transplant indication and DGF are historical; "post-op course"
  is historical. They are clinically motivated, but they are tuned to this generator.
  Before them, medspaCy scored 88.0% on temporality and 94.0% on concepts.
- **The ground truth comes from the same generator,** so there is no annotation noise and no
  disagreement between annotators. Real evaluation needs clinician-annotated notes.
- **"Graft tenderness" has no SNOMED concept** in our vocabulary. The LLM finds it, but it is
  stored with concept 0 and goes to review.
- **Concept accuracy** counts a mapping as correct when it is the expected concept or one of
  its descendants: "rejection" may be any transplanted-organ rejection.
- **The LLM sees one de-identified sentence at a time.** It never sees a whole note,
  identifiers or dates. Its findings must quote the sentence, and its concept comes from our
  vocabulary.
