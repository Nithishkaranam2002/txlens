import { describe, expect, it } from "vitest";

import { applyReview } from "./queries";
import type { Flag, WorklistPage } from "./types";

function flag(id: string, status: Flag["status"] = "new"): Flag {
  return {
    flag_id: id, rule_id: "r", config_id: "c", config_version: 1, severity: "high", reason: "x",
    evidence: [], as_of: "2025-06-30", status, status_changed_at: null, status_changed_by: null,
    note: null, cleared: false,
  };
}

function page(): WorklistPage {
  const patient = { patient_id: "R-1", age_band: "40-44", sex: null, organ: "kidney" as const,
    donor_type: null, status: "active" as const, transplant_date: null, death_date: null };
  return {
    items: [
      { patient, score: 14, flags: [flag("a"), flag("b")] },
      { patient: { ...patient, patient_id: "R-2" }, score: 4, flags: [flag("c")] },
    ],
    total: 2, limit: 50, offset: 0,
  };
}

describe("applyReview (optimistic worklist update)", () => {
  it("marks an acknowledged flag and keeps the note", () => {
    const next = applyReview(page(), { flagId: "a", action: "acknowledge", note: "calling" });
    expect(next.items[0].flags[0]).toMatchObject({ status: "acknowledged", note: "calling" });
    expect(next.total).toBe(2);
  });

  it("removes a resolved flag, and the patient when no flag is left", () => {
    const next = applyReview(page(), { flagId: "c", action: "resolve" });
    expect(next.items.map((r) => r.patient.patient_id)).toEqual(["R-1"]);
    expect(next.total).toBe(1);
  });
});
