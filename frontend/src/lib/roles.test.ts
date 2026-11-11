import { describe, expect, it } from "vitest";

import type { Route } from "next";

import { asRole, can, homeFor, landingFor, navFor, seesIdentifiers } from "./roles";

describe("role navigation", () => {
  it.each([
    ["clinician", ["Worklist", "Cohorts"], "/worklist"],
    ["data_steward", ["Worklist"], "/worklist"],
    ["researcher", ["Cohorts"], "/cohorts"],
    ["admin", ["Admin"], "/admin"],
  ] as const)("%s sees %j and lands on %s", (role, labels, home) => {
    expect(navFor(role).map((item) => item.label)).toEqual(labels);
    expect(homeFor(role)).toBe(home);
  });

  it("only clinicians see identifiers or review flags", () => {
    expect(seesIdentifiers("clinician")).toBe(true);
    for (const role of ["researcher", "data_steward", "admin"] as const) {
      expect(seesIdentifiers(role)).toBe(false);
      expect(can(role, "review")).toBe(false);
    }
  });

  it("admins cannot reach patient data and unknown roles get nothing", () => {
    expect(can("admin", "patients")).toBe(false);
    expect(asRole("superuser")).toBeNull();
    expect(navFor(null)).toEqual([]);
  });
});

describe("landing page after login", () => {
  it.each([
    ["clinician", "/worklist?severity=high", "/worklist?severity=high"],
    ["clinician", "/patients/42", "/patients/42"],
    ["researcher", "/cohorts/7", "/cohorts/7"],
    ["admin", "/worklist", "/admin"],
    ["researcher", "/worklist", "/cohorts"],
    ["data_steward", "/cohorts", "/worklist"],
    ["clinician", "/admin", "/worklist"],
    ["clinician", "/", "/worklist"],
    ["admin", null, "/admin"],
  ] as const)("%s with next=%s goes to %s", (role, next, expected) => {
    expect(landingFor(role, next as Route | null)).toBe(expected);
  });
});
