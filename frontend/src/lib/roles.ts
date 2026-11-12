import type { Route } from "next";

import type { components } from "@/lib/api/schema";

export type Role = "clinician" | "researcher" | "data_steward" | "admin";
export type Me = components["schemas"]["Me"];

/** "review": act on surveillance flags; "queue": the human review queue; "metrics": its numbers. */
export type Area =
  | "worklist"
  | "cohorts"
  | "survival"
  | "patients"
  | "flags"
  | "review"
  | "queue"
  | "metrics"
  | "admin";

/** What each role may use. The API enforces the same rules; this only shapes the UI. */
const ACCESS: Record<Role, readonly Area[]> = {
  clinician: ["worklist", "cohorts", "survival", "patients", "flags", "review", "queue", "metrics"],
  data_steward: ["worklist", "patients", "flags", "queue", "metrics"],
  researcher: ["cohorts", "survival", "patients"],
  admin: ["admin", "metrics"],
};

export const NAV: readonly { href: Route; label: string; area: Area }[] = [
  { href: "/worklist", label: "Worklist", area: "worklist" },
  { href: "/review", label: "Review queue", area: "queue" },
  { href: "/cohorts", label: "Cohorts", area: "cohorts" },
  { href: "/survival", label: "Survival", area: "survival" },
  { href: "/admin", label: "Admin", area: "admin" },
  { href: "/review/metrics", label: "Review metrics", area: "metrics" },
];

export const ROLE_LABEL: Record<Role, string> = {
  clinician: "Clinician",
  researcher: "Researcher",
  data_steward: "Data steward",
  admin: "Administrator",
};

export function asRole(value: string): Role | null {
  return value in ACCESS ? (value as Role) : null;
}

export function can(role: Role | null, area: Area): boolean {
  return role !== null && ACCESS[role].includes(area);
}

/** Only clinicians see names, MRNs, birth dates and real dates. */
export function seesIdentifiers(role: Role | null): boolean {
  return role === "clinician";
}

export function navFor(role: Role | null) {
  return NAV.filter((item) => can(role, item.area));
}

/** The first page a role lands on after login. */
export function homeFor(role: Role | null): Route {
  return navFor(role)[0]?.href ?? "/admin";
}

/** The area a signed-in page belongs to, or null for a path outside every area. */
export function areaOf(path: string): Area | null {
  const [, segment, sub] = path.split(/[/?#]/);
  if (segment === "review") return sub === "metrics" ? "metrics" : "queue";
  const areas: readonly string[] = ["worklist", "cohorts", "survival", "patients", "admin"];
  return areas.includes(segment) ? (segment as Area) : null;
}

/** Where to go after login: the requested page if this role may use it, else the role's home. */
export function landingFor(role: Role | null, next: Route | null): Route {
  const area = next && areaOf(next);
  return area && can(role, area) ? next : homeFor(role);
}
