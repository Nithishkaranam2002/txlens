# TEST ONLY: fake Athena vocabulary fixture

These files mimic the Athena download format (tab-separated, header row, YYYYMMDD dates) so
the loader, subset extractor and lookups can be tested without licensed content.

- **Everything here is fake.** Concept ids are 2,100,000,000 and up (OMOP's range for local
  concepts), every name starts with `TEST ONLY -`, and every code starts with `TEST-`.
  None of it is real SNOMED, LOINC, RxNorm or ICD-10-CM content, so it can be committed.
- Never load this into a database used for real work.

What the fixture covers:

| Case | Concepts (id - 2,100,000,000) |
|---|---|
| Hierarchy: transplant rejection > kidney rejection > TCMR / ABMR; heart rejection | 1-5 |
| ICD10CM source code that "Maps to" a standard SNOMED concept | 10 -> 2 |
| Outdated SNOMED code that "Maps to" its replacement | 8 -> 2 |
| Unmapped source code (its only mapping is deprecated) | 12 |
| LOINC code in the wrong domain (Observation) | 22 |
| RxNorm ingredient with clinical-drug descendants | 30 > 31, 32 |
| CPT4 concept with an empty name (excluded vocabulary) | 40 |
| NDC concept (vocabulary not loaded) | 50 |
| No UCUM concepts (missing-vocabulary warning) | - |
