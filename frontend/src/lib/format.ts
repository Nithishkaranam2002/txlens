import { isIdentified, type Patient } from "@/lib/types";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const CALENDAR_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
// No timeZone option: the viewer's (browser's) time zone.
const localDate = (date: Date) =>
  date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
const localTime = (date: Date) =>
  date.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZoneName: "short" });

function instant(value: string): Date | null {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/**
 * A date as "1 Mar 2025". A calendar date ("2025-03-01": transplant, lab, note dates) is shown
 * as it is, never shifted. A timestamp ("2026-11-15T01:30:00Z": created, queued, decided) is
 * shown as the date in the viewer's time zone.
 */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const calendar = CALENDAR_DATE.exec(value);
  if (calendar) return `${Number(calendar[3])} ${MONTHS[Number(calendar[2]) - 1]} ${calendar[1]}`;
  const date = instant(value);
  return date ? localDate(date) : value;
}

/** A timestamp as "14 Nov 2026, 7:30 PM CST" in the viewer's time zone (a calendar date: no time). */
export function formatDateTime(value: string | null | undefined): string {
  if (!value || CALENDAR_DATE.test(value)) return formatDate(value);
  const date = instant(value);
  return date ? `${localDate(date)}, ${localTime(date)}` : value;
}

/** "Updated just now", "Updated 4 min ago", "Updated 2 h ago" (times in milliseconds). */
export function formatUpdated(updatedAt: number, now: number): string {
  const minutes = Math.floor(Math.max(0, now - updatedAt) / 60_000);
  if (minutes < 1) return "Updated just now";
  if (minutes < 60) return `Updated ${minutes} min ago`;
  return `Updated ${Math.floor(minutes / 60)} h ago`;
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
