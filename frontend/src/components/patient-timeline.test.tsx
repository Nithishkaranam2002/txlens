import { describe, expect, it } from "vitest";

import type { Timeline } from "@/lib/types";

import { biopsyResultText, timelineEvents } from "./patient-timeline";

const timeline: Timeline = {
  patient: { patient_id: "R-1", age_band: "50-54", sex: null, organ: "kidney", donor_type: null,
    status: "deceased", transplant_date: "2024-01-10", death_date: "2025-05-01" },
  labs: [], drugs: [], notes: [],
  conditions: [
    { date: "2024-01-10", concept_id: 1, condition: "End-stage renal disease" },
    { date: "2025-03-01", concept_id: 2, condition: "Failed renal transplant" },
  ],
  procedures: [
    { date: "2024-01-10", kind: "transplant", concept_id: 3, procedure: "Renal transplant", result: null },
    { date: "2024-06-01", kind: "biopsy", concept_id: 4, procedure: "Graft biopsy", result: null },
    { date: "2024-07-01", kind: "biopsy", concept_id: 4, procedure: "Graft biopsy", result: {
      status: "in_review", diagnosis: "TCMR", grade: "IA", scores: { i: "2", t: "2" },
      additional_finding: null, document_id: "d1", evidence_url: "/documents/d1/evidence" } },
    { date: "2024-08-01", kind: "other", concept_id: 5, procedure: "Something", result: null },
  ],
};

describe("biopsyResultText", () => {
  const base = { additional_finding: null, document_id: null, evidence_url: null } as const;

  it("names heart grades and leaves out the grade for no rejection", () => {
    expect(biopsyResultText({ ...base, status: "approved", diagnosis: "TCMR", grade: "2R",
      scores: { ACR: "2R", pAMR: "1 (I+)" } })).toBe("Result: TCMR 2R · ACR 2R pAMR 1 (I+) · approved");
    expect(biopsyResultText({ ...base, status: "extracted", diagnosis: "no_rejection", grade: null,
      scores: {} })).toBe("Result: No rejection · extracted, not reviewed");
  });

  it("says when a report is not read yet", () => {
    expect(biopsyResultText(null)).toBe("Result: awaiting document extraction");
  });
});

describe("timelineEvents", () => {
  it("lists transplant, biopsies with results, graft events and death, newest first", () => {
    const events = timelineEvents(timeline).map((e) => [e.date, e.kind, e.detail ?? null]);
    expect(events).toEqual([
      ["2025-05-01", "Death", null],
      ["2025-03-01", "Graft event", null],
      ["2024-07-01", "Biopsy", "Result: TCMR IA · i2 t2 · in review"],
      ["2024-06-01", "Biopsy", "Result: awaiting document extraction"],
      ["2024-01-10", "Transplant", null],
      ["2024-01-10", "Diagnosis", null],
    ]);
  });
});
