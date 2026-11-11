# OCR of biopsy reports (no LLM)

Generated 2026-11-11 by `make ocr-report` (evals/src/txlens_evals/ocr_benchmark.py). Synthetic data only.

**Best setup: generated PDFs: I. D + whitelist re-reads (production default) reads 96.7% of key tokens exactly.**

100 reports (the extraction eval's stratified sample: ~60% kidney, ~40%
heart). A key token counts when it appears exactly in the OCR text: a Banff score such
as `t2`, C4d, the TCMR grade, adequacy, the procedure date, the ISHLT ACR and pAMR
grades, and the exact diagnosis and finding wording.

| Setup | Key tokens exact | Accuracy | Reports all exact | Seconds per report |
|---|---|---|---|---|
| generated PDFs: A. Step 2.1 start (400 dpi, denoise, psm 4) | 713/890 | **80.1%** | 27/100 | 1.74 |
| generated PDFs: B. 300 dpi only | 713/890 | **80.1%** | 29/100 | 0.87 |
| generated PDFs: C. 300 dpi + deskew | 701/890 | **78.8%** | 28/100 | 1.11 |
| generated PDFs: D. 300 dpi + deskew + denoise | 708/890 | **79.6%** | 28/100 | 1.42 |
| generated PDFs: E. D + Otsu | 680/890 | **76.4%** | 25/100 | 1.48 |
| generated PDFs: F. D + adaptive threshold | 595/890 | **66.9%** | 19/100 | 1.49 |
| generated PDFs: G. D + 2x upscale | 700/890 | **78.7%** | 28/100 | 3.12 |
| generated PDFs: H. D + psm 6 | 709/890 | **79.7%** | 28/100 | 1.73 |
| generated PDFs: I. D + whitelist re-reads (production default) | 861/890 | **96.7%** | 78/100 | 1.79 |
| generated PDFs: J. A + whitelist re-reads | 843/890 | **94.7%** | 68/100 | 2.37 |
| generated PDFs: K. 300 dpi + denoise + whitelist re-reads | 856/890 | **96.2%** | 77/100 | 1.84 |
| re-rendered at 120 dpi: A. Step 2.1 start (400 dpi, denoise, psm 4) | 580/890 | **65.2%** | 21/100 | 1.90 |
| re-rendered at 120 dpi: B. 300 dpi only | 585/890 | **65.7%** | 23/100 | 1.07 |
| re-rendered at 120 dpi: C. 300 dpi + deskew | 581/890 | **65.3%** | 17/100 | 1.36 |
| re-rendered at 120 dpi: D. 300 dpi + deskew + denoise | 627/890 | **70.4%** | 20/100 | 1.86 |
| re-rendered at 120 dpi: E. D + Otsu | 521/890 | **58.5%** | 11/100 | 1.13 |
| re-rendered at 120 dpi: F. D + adaptive threshold | 412/890 | **46.3%** | 6/100 | 1.38 |
| re-rendered at 120 dpi: G. D + 2x upscale | 605/890 | **68.0%** | 22/100 | 2.98 |
| re-rendered at 120 dpi: H. D + psm 6 | 591/890 | **66.4%** | 24/100 | 1.74 |
| re-rendered at 120 dpi: I. D + whitelist re-reads (production default) | 705/890 | **79.2%** | 40/100 | 1.50 |
| re-rendered at 120 dpi: J. A + whitelist re-reads | 638/890 | **71.7%** | 25/100 | 1.46 |
| re-rendered at 120 dpi: K. 300 dpi + denoise + whitelist re-reads | 690/890 | **77.5%** | 41/100 | 1.11 |
| re-rendered at 300 dpi: A. Step 2.1 start (400 dpi, denoise, psm 4) | 689/890 | **77.4%** | 23/100 | 1.67 |
| re-rendered at 300 dpi: B. 300 dpi only | 688/890 | **77.3%** | 25/100 | 0.78 |
| re-rendered at 300 dpi: C. 300 dpi + deskew | 682/890 | **76.6%** | 25/100 | 1.01 |
| re-rendered at 300 dpi: D. 300 dpi + deskew + denoise | 680/890 | **76.4%** | 19/100 | 1.27 |
| re-rendered at 300 dpi: E. D + Otsu | 663/890 | **74.5%** | 24/100 | 1.05 |
| re-rendered at 300 dpi: F. D + adaptive threshold | 665/890 | **74.7%** | 29/100 | 1.21 |
| re-rendered at 300 dpi: G. D + 2x upscale | 671/890 | **75.4%** | 7/100 | 2.38 |
| re-rendered at 300 dpi: H. D + psm 6 | 679/890 | **76.3%** | 19/100 | 1.30 |
| re-rendered at 300 dpi: I. D + whitelist re-reads (production default) | 790/890 | **88.8%** | 56/100 | 1.50 |
| re-rendered at 300 dpi: J. A + whitelist re-reads | 802/890 | **90.1%** | 62/100 | 1.71 |
| re-rendered at 300 dpi: K. 300 dpi + denoise + whitelist re-reads | 818/890 | **91.9%** | 62/100 | 1.18 |

## By kind of token (best setup)

| Setup | Kind | Exact | Accuracy |
|---|---|---|---|
| generated PDFs: I. D + whitelist re-reads (production default) | Banff score | 440/465 | 94.6% |
| generated PDFs: I. D + whitelist re-reads (production default) | C4d | 60/60 | 100.0% |
| generated PDFs: I. D + whitelist re-reads (production default) | ISHLT ACR grade | 40/40 | 100.0% |
| generated PDFs: I. D + whitelist re-reads (production default) | TCMR grade | 17/20 | 85.0% |
| generated PDFs: I. D + whitelist re-reads (production default) | adequacy | 43/43 | 100.0% |
| generated PDFs: I. D + whitelist re-reads (production default) | diagnosis wording | 108/108 | 100.0% |
| generated PDFs: I. D + whitelist re-reads (production default) | finding wording | 14/14 | 100.0% |
| generated PDFs: I. D + whitelist re-reads (production default) | pAMR grade | 40/40 | 100.0% |
| generated PDFs: I. D + whitelist re-reads (production default) | procedure date | 99/100 | 99.0% |

## Most frequent misses (best setup)

| Token | Reports |
|---|---|
| Banff score: ptc0 | 9 |
| Banff score: ct0 | 6 |
| Banff score: v0 | 4 |
| Banff score: g0 | 4 |
| TCMR grade: IIA | 3 |
| Banff score: i0 | 2 |
| procedure date: 01/17/2024 | 1 |

## Notes

- `generated PDFs` are the files in data/raw (scanned at the generator's pdf_dpi, 200
  since Step 2.1; 120 before, which read at most 72% with the best setup).
  `re-rendered` rows read the same reports drawn at another scan resolution
  (data/eval/), everything else equal.
- Whitelist re-reads read tokens after known anchors ("scores:", "capillaries):",
  "Banff grade", "ISHLT 2004):") a second time, allowing only the characters such a
  token can contain. That uses knowledge of this report template; real reports would
  need it per template.
- Exact match is strict: "gO" for g0 is a miss here, although the evidence check of
  the extraction tolerates it.
