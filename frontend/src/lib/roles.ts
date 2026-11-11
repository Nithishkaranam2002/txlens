import type { Route } from "next";

import type { components } from "@/lib/api/schema";

export type Role = "clinician" | "researcher" | "data_steward" | "admin";
export type Me = components["schemas"]["Me"];

export type Area = "worklist" | "cohorts" | "patients" | "flags" | "review" | "admin";

/** What each role may use. The API enforces the same rules; this only shapes the UI. */
const ACCESS: Record<Role, readonly Area[]> = {
  clinician: ["worklist", "cohorts", "patients", "flags", "review"],
  data_steward: ["worklist", "patients", "flags"],
  researcher: ["cohorts", "patients"],
  admin: ["admin"],
};

export const NAV: readonly { href: Route; label: string; area: Area }[] = [
  { href: "/worklist", label: "Worklist", area: "worklist" },
  { href: "/cohorts", label: "Cohorts", area: "cohorts" },
  { href: "/admin", label: "Admin", area: "admin" },
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
  const segment = path.split(/[/?#]/)[1];
  return segment === "worklist" || segment === "cohorts" || segment === "patients" || segment === "admin"
    ? segment
    : null;
}

/** Where to go after login: the requested page if this role may use it, else the role's home. */
export function landingFor(role: Role | null, next: Route | null): Route {
  const area = next && areaOf(next);
  return area && can(role, area) ? next : homeFor(role);
}
