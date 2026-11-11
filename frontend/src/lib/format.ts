import { isIdentified, type Patient } from "@/lib/types";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2025-03-01" (or an ISO datetime) as "1 Mar 2025", without time-zone shifts. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const [year, month, day] = value.slice(0, 10).split("-").map(Number);
  if (!year || !month || !day) return value;
  return `${day} ${MONTHS[month - 1]} ${year}`;
}

/** A display name: "Family, Given" for clinicians; the pseudonym for everyone else. */
export function patientName(patient: Patient): string {
  if (!isIdentified(patient)) return String(patient.patient_id);
  const name = [patient.family_name, patient.given_name].filter(Boolean).join(", ");
  return name || `Patient ${patient.patient_id}`;
}

/** The second line under a name: MRN for clinicians, age band for de-identified views. */
export function patientDetail(patient: Patient): string {
  if (isIdentified(patient)) return patient.mrn ? `MRN ${patient.mrn}` : "";
  return patient.age_band ? `Age ${patient.age_band}` : "";
}

/** A count from the API; "<11" (small-cell suppression) reads as "fewer than 11". */
export function formatCount(count: number | string): string {
  if (typeof count === "number") return count.toLocaleString("en-US");
  return count.startsWith("<") ? `fewer than ${count.slice(1)}` : count;
}

/** Counts as numbers for charts; suppressed counts become null (drawn as "<11", not 0). */
export function countValue(count: number | string): number | null {
  return typeof count === "number" ? count : null;
}

export const ORGAN_LABEL: Record<string, string> = { kidney: "Kidney", heart: "Heart" };
export const STATUS_LABEL: Record<string, string> = {
  active: "Active",
  graft_failure: "Graft failure",
  deceased: "Deceased",
};

export function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1).replaceAll("_", " ");
}
