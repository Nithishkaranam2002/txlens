import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { Timeline } from "@/lib/types";

import { FromNoteBadge, biopsyResultText, timelineEvents } from "./patient-timeline";

const timeline: Timeline = {
  patient: { patient_id: "R-1", age_band: "50-54", sex: null, organ: "kidney", donor_type: null,
    status: "deceased", transplant_date: "2024-01-10", death_date: "2025-05-01" },
  labs: [], drugs: [], notes: [],
  conditions: [
    { date: "2024-01-10", concept_id: 1, condition: "End-stage renal disease", source: "record" },
    { date: "2025-03-01", concept_id: 2, condition: "Failed renal transplant", source: "record" },
    { date: "2024-09-01", concept_id: 6, condition: "Hypertension", source: "note", note_count: 3,
      evidence: { date: "2024-09-01", note_id: null, sentence: null } },
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
      ["2024-09-01", "Diagnosis", null],
      ["2024-07-01", "Biopsy", "Result: TCMR IA · i2 t2 · in review"],
      ["2024-06-01", "Biopsy", "Result: awaiting document extraction"],
      ["2024-01-10", "Transplant", null],
      ["2024-01-10", "Diagnosis", null],
    ]);
  });
});

describe("FromNoteBadge", () => {
  it("marks note conditions and keeps record conditions unmarked", () => {
    const marked = timelineEvents(timeline).filter((e) => e.fromNote).map((e) => [e.title, e.fromNote?.count]);
    expect(marked).toEqual([["Hypertension", 3]]);
  });

  it("shows the sentence in a popover when the role may see it", async () => {
    render(<FromNoteBadge fromNote={{ count: 2, evidence: { date: "2024-09-01", note_id: 7,
      sentence: "HPI: Ada reports graft tenderness." } }} />);
    expect(screen.queryByText(/graft tenderness/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /from note: show the sentence \(mentioned in 2 notes\)/i }));
    expect(screen.getByText("HPI: Ada reports graft tenderness.")).toBeInTheDocument();
  });

  it("shows only the badge and the count without a sentence (researchers)", () => {
    render(<FromNoteBadge fromNote={{ count: 1, evidence: { date: "2024-09-01", note_id: null, sentence: null } }} />);
    expect(screen.getByText("from note")).toBeInTheDocument();
    expect(screen.getByText("mentioned in 1 note")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
