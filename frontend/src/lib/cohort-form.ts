import type { CohortRules, ConceptHit, LabRule } from "@/lib/types";

export type LabRuleDraft = { lab: LabRule["lab"]; op: LabRule["op"]; value: string; dayFrom: string; dayTo: string };

export type CohortForm = {
  organ?: "kidney" | "heart";
  donorType?: "living" | "deceased";
  ageMin: string;
  ageMax: string;
  conditions: ConceptHit[];
  labs: LabRuleDraft[];
  yearFrom: string;
  yearTo: string;
};

export const EMPTY_FORM: CohortForm = { ageMin: "", ageMax: "", conditions: [], labs: [], yearFrom: "", yearTo: "" };

export const LAB_OPTIONS: { value: LabRule["lab"]; label: string; unit: string }[] = [
  { value: "dd_cfdna", label: "dd-cfDNA", unit: "%" },
  { value: "creatinine", label: "Creatinine", unit: "mg/dL" },
  { value: "egfr", label: "eGFR", unit: "mL/min/1.73m²" },
  { value: "tacrolimus_trough", label: "Tacrolimus trough", unit: "ng/mL" },
  { value: "dsa_mfi", label: "DSA max MFI", unit: "MFI" },
];
export const OP_OPTIONS: { value: LabRule["op"]; label: string }[] = [
  { value: ">=", label: "≥" },
  { value: ">", label: ">" },
  { value: "<=", label: "≤" },
  { value: "<", label: "<" },
];

export const NEW_LAB_RULE: LabRuleDraft = { lab: "dd_cfdna", op: ">=", value: "1", dayFrom: "0", dayTo: "365" };

function integer(value: string): number | null {
  if (value.trim() === "") return null;
  const n = Number(value);
  return Number.isInteger(n) ? n : null;
}

/**
 * The API request for a form. Empty fields are left out. Years become whole calendar years
 * (Jan 1 – Dec 31), which is also all researchers may ask for (exact dates could re-identify).
 */
export function buildRules(form: CohortForm): CohortRules {
  const rules: CohortRules = {};
  if (form.organ) rules.organ = form.organ;
  if (form.donorType) rules.donor_type = form.donorType;
  const ageMin = integer(form.ageMin);
  const ageMax = integer(form.ageMax);
  if (ageMin !== null) rules.age_min = ageMin;
  if (ageMax !== null) rules.age_max = ageMax;
  if (form.conditions.length) rules.condition_concept_ids = form.conditions.map((c) => c.concept_id);
  const labs = form.labs
    .filter((lab) => lab.value.trim() !== "" && Number.isFinite(Number(lab.value)))
    .map((lab) => ({
      lab: lab.lab,
      op: lab.op,
      value: Number(lab.value),
      days_from_transplant: [integer(lab.dayFrom) ?? 0, integer(lab.dayTo) ?? 365] as [number, number],
    }));
  if (labs.length) rules.lab_rules = labs;
  const from = integer(form.yearFrom);
  const to = integer(form.yearTo);
  if (from !== null || to !== null) {
    rules.transplant_date = {
      ...(from !== null && { start: `${from}-01-01` }),
      ...(to !== null && { end: `${to}-12-31` }),
    };
  }
  return rules;
}

const OP_TEXT: Record<LabRule["op"], string> = { ">=": "≥", ">": ">", "<=": "≤", "<": "<" };

/** Rules as read back from the API (response arrays are plain arrays, not tuples). */
type SavedRules = Omit<CohortRules, "lab_rules"> & {
  lab_rules?: (Omit<LabRule, "days_from_transplant"> & { days_from_transplant: number[] })[];
};

/** Saved rules in plain English, for the saved-cohort page. */
export function describeRules(rules: SavedRules): string[] {
  const out: string[] = [];
  if (rules.organ) out.push(`Organ: ${rules.organ}`);
  if (rules.donor_type) out.push(`Donor: ${rules.donor_type}`);
  if (rules.age_min != null || rules.age_max != null) {
    out.push(`Age at transplant: ${rules.age_min ?? 0}–${rules.age_max ?? 120}`);
  }
  if (rules.transplant_date) {
    const start = rules.transplant_date.start?.slice(0, 4) ?? "any";
    const end = rules.transplant_date.end?.slice(0, 4) ?? "any";
    out.push(`Transplant year: ${start}–${end}`);
  }
  const n = rules.condition_concept_ids?.length ?? 0;
  if (n) out.push(`Has ${n === 1 ? "a condition" : `one of ${n} conditions`} (including more specific ones)`);
  for (const lab of rules.lab_rules ?? []) {
    const label = LAB_OPTIONS.find((o) => o.value === lab.lab);
    const [from, to] = lab.days_from_transplant;
    out.push(`${label?.label ?? lab.lab} ${OP_TEXT[lab.op]} ${lab.value} ${label?.unit ?? ""} on days ${from}–${to} after transplant`.replace("  ", " "));
  }
  return out.length ? out : ["All patients"];
}

/** An attrition step label with concept ids replaced by the chosen names and lab keys by labels. */
export function labelStep(rule: string, conditions: ConceptHit[]): string {
  const names = new Map(conditions.map((c) => [String(c.concept_id), c.name]));
  let label = rule.replace(/\[(\d+(?:, \d+)*)\]/, (match, ids: string) => {
    const named = ids.split(", ").map((id) => names.get(id));
    return named.every(Boolean) ? named.join(" or ") : match;
  });
  for (const option of LAB_OPTIONS) label = label.replace(new RegExp(`\\b${option.value}\\b`), option.label);
  return label;
}
