import { describe, expect, it } from "vitest";

import type { Route } from "next";

import { areaOf, asRole, can, homeFor, landingFor, navFor, seesIdentifiers } from "./roles";

describe("role navigation", () => {
  it.each([
    ["clinician", ["Worklist", "Review queue", "Cohorts", "Survival", "Copilot", "Review metrics"], "/worklist"],
    ["data_steward", ["Worklist", "Review queue", "Review metrics"], "/worklist"],
    ["researcher", ["Cohorts", "Survival", "Copilot"], "/cohorts"],
    ["admin", ["Admin", "Review metrics"], "/admin"],
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

describe("review queue access", () => {
  it("clinicians and data stewards use the queue; admins see its metrics only", () => {
    expect(can("clinician", "queue") && can("data_steward", "queue")).toBe(true);
    expect(can("researcher", "queue") || can("admin", "queue")).toBe(false);
    expect(can("admin", "metrics") && !can("researcher", "metrics")).toBe(true);
    expect(areaOf("/review/biopsy_document/abc")).toBe("queue");
    expect(areaOf("/review/metrics")).toBe("metrics");
  });
});

describe("survival analysis access", () => {
  it("clinicians and researchers analyse; data stewards and admins do not", () => {
    expect(can("clinician", "survival") && can("researcher", "survival")).toBe(true);
    expect(can("data_steward", "survival") || can("admin", "survival")).toBe(false);
    expect(areaOf("/survival?cohort=abc")).toBe("survival");
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

describe("copilot access", () => {
  it("is for clinicians and researchers only", () => {
    expect(can("clinician", "copilot") && can("researcher", "copilot")).toBe(true);
    expect(can("data_steward", "copilot") || can("admin", "copilot")).toBe(false);
    expect(areaOf("/copilot?c=abc")).toBe("copilot");
  });
});

describe("rejection-risk scores", () => {
  it("clinicians and data stewards see individual scores; researchers and admins never do", () => {
    expect(can("clinician", "risk") && can("data_steward", "risk")).toBe(true);
    expect(can("researcher", "risk") || can("admin", "risk")).toBe(false);
    expect(can("clinician", "rescore")).toBe(true);
    expect(can("data_steward", "rescore") || can("researcher", "rescore")).toBe(false);
  });
});
