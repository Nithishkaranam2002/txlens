# ADR 0009: Config-driven surveillance engine with evidence-keyed flags

**Status:** accepted (2026-11-10)

## Context
Transplant programs monitor patients on a schedule: dd-cfDNA at months 1, 2, 3, 4, 6, 9 and 12,
then quarterly, plus creatinine/eGFR, tacrolimus levels and donor-specific antibodies.
Clinicians need a worklist of patients to review. The same idea applies to other diseases,
for example a tumor DNA marker after cancer treatment. CLAUDE.md requires that:
- the engine is disease-agnostic;
- rules and thresholds are deterministic code, never an LLM;
- every flag links to its source data.

## Options compared

| | **YAML configs + small pure-Python rule kinds** (chosen) | Rules hard-coded per organ | General rule engine (Drools-style, JSON Logic) | LLM scoring |
|---|---|---|---|---|
| New disease without code change | Yes (new YAML) | No | Yes | Yes |
| Easy to review and test | Yes: 7 small functions | Yes | Harder: a rule language to learn | No |
| Deterministic, explainable | Yes | Yes | Yes | No (hard rule violation) |
| Plain-English reasons with evidence | Built in | Built in | Extra work | Unreliable |

## Decision
- **Configs** live in `backend/src/txlens_backend/surveillance/configs/*.yaml`. Each has an
  `id`, a `version`, the markers (found by vocabulary code), the schedule and the rules.
  Pydantic validates them at startup, rejecting unknown keys, unknown markers and duplicate
  rule ids.
- **Seven rule kinds**: `schedule_adherence`, `threshold`, `rising_trend`, `relative_change`
  (against the patient's own baseline), `range_by_phase`, `new_positive` and `detected`.
  Kidney, heart and oncology differ only in YAML.
- **The engine is pure**: `evaluate(config, series, as_of)` sees only data collected on or
  before `as_of`. The nightly job, the API and the evaluation run the same code.
- **Flags** carry the config id and version, a rule id, a severity, a plain-English reason and
  the evidence (each result's marker, date and value, or the missed due date).
- **Idempotency key** = SHA-256 of the rule id plus the evidence. Values are rounded to 3
  decimals, so float noise from unit conversion does not matter. The config version is left
  out, so a new config version never reopens a flag that was already resolved for the same
  results. The table has a unique key on (center, patient, evidence key), and re-runs use
  `ON CONFLICT`.
- **Lifecycle**: clinicians move a flag `new → acknowledged → resolved`, or straight from
  `new` to `resolved`. Resolved is final, and every change is audited. When a rule stops
  firing, the engine sets `cleared_at`; the flag is never deleted. A replay of an older date
  never clears flags created for a later date.
- **Priority score**: the sum of severity weights per rule group, with each group counted
  once. Creatinine and eGFR measure the same thing, so a patient with both flags is not
  ranked above everyone else.

## Consequences
- Adding a disease means writing a YAML file and loading its data. `oncology_tumor_dna.yaml`
  runs on the generator's oncology cohort with no code change.
- Only transplant programs are loaded from OMOP today. The oncology cohort is evaluated from
  CSV.
- A test checks that the database path (OMOP + engine) and the replay path (ground-truth CSV
  + engine) produce the same rule ids and evidence keys.
- Data stewards see flags de-identified. Every date in the evidence and in the reason text is
  shifted by the patient's offset, and clinicians' free-text notes are hidden.
