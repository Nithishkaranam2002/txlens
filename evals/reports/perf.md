# API and page latency (after Step 4.1 fixes)

Full demo data (2,000 patients, 3 centers). Production builds in Docker on one laptop; times include the HTTP round trip. Re-run: `make perf`.

| Endpoint | Role | before p50 / p95 / max | p50 ms | p95 ms | max ms | runs | status |
|---|---|---:|---:|---:|---:|---:|---:|
| survival analysis | researcher | 81 / 82 / 82 | 83 | 83 | 83 | 5 | 200 |
| worklist, de-identified | data_steward | 30 / 33 / 160 | 23 | 27 | 32 | 20 | 200 |
| worklist (default: high + medium) | clinician | 29 / 49 / 302 | 20 | 22 | 22 | 20 | 200 |
| worklist, all severities, page 2 | clinician | 26 / 27 / 158 | 20 | 22 | 22 | 20 | 200 |
| worklist, low only | clinician | 26 / 34 / 155 | 20 | 22 | 22 | 20 | 200 |
| review queue | clinician | 19 / 22 / 22 | 19 | 19 | 20 | 20 | 200 |
| patient timeline | clinician | 8 / 8 / 8 | 7 | 12 | 26 | 20 | 200 |
| patient flags, de-identified | data_steward | 10 / 10 / 11 | 10 | 11 | 11 | 20 | 200 |
| patient flags | clinician | 9 / 9 / 9 | 9 | 10 | 10 | 20 | 200 |
| patient timeline, de-identified | data_steward | 8 / 9 / 10 | 8 | 8 | 9 | 20 | 200 |
| cohort members | clinician | 6 / 8 / 8 | 7 | 8 | 8 | 20 | 200 |
| patient list | clinician | 6 / 6 / 6 | 7 | 8 | 8 | 20 | 200 |
| cohort preview | researcher | 7 / 8 / 8 | 6 | 7 | 8 | 20 | 200 |
| copilot conversations | researcher | 2 / 2 / 3 | 2 | 3 | 3 | 20 | 200 |
| patient risk | clinician | 2 / 3 / 3 | 2 | 2 | 3 | 20 | 200 |

| Page | before p50 | p50 ms (until its data is shown) | max ms | runs |
|---|---:|---:|---:|---:|
| Worklist (clinician) | 161 | 137 | 260 | 20 |
| Patient page | 138 | 134 | 206 | 20 |
| Review queue | 107 | 107 | 117 | 20 |
| Cohort builder | 43 | 39 | 62 | 20 |
| Survival analysis | 42 | 40 | 65 | 20 |
