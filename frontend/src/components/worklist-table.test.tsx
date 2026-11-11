import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { renderWithQuery } from "@/test/render";
import type { WorklistRow } from "@/lib/types";

import { WorklistTable } from "./worklist-table";

const base = { sex: "female", organ: "kidney" as const, donor_type: "living" as const, status: "active" as const,
  transplant_date: "2024-01-10", death_date: null };
const flag = {
  flag_id: "f1", rule_id: "dd_cfdna_threshold", config_id: "kidney_transplant", config_version: 1,
  severity: "high" as const, reason: "dd-cfDNA 1.2% on 2025-03-01 is at or above the 1% threshold",
  evidence: [], as_of: "2025-06-30", status: "new" as const, status_changed_at: null,
  status_changed_by: null, note: null, cleared: false,
};
const identified: WorklistRow[] = [
  { patient: { ...base, patient_id: 7, mrn: "MRN-0007", given_name: "Ada", family_name: "Vale", birth_date: "1970-02-03" },
    score: 10, flags: [flag] },
  { patient: { ...base, patient_id: 8, mrn: "MRN-0008", given_name: "Bo", family_name: "Ash", birth_date: "1980-02-03" },
    score: 4, flags: [{ ...flag, flag_id: "f2", severity: "medium" as const, status: "acknowledged" as const }] },
];

describe("WorklistTable", () => {
  it("shows names, severity as text, reasons and review buttons for clinicians", () => {
    renderWithQuery(<WorklistTable rows={identified} offset={0} canReview />);
    const first = screen.getAllByRole("row")[1];
    expect(within(first).getByRole("link", { name: "Vale, Ada" })).toHaveAttribute("href", "/patients/7");
    expect(within(first).getByText("MRN MRN-0007 · Kidney")).toBeInTheDocument();
    expect(within(first).getByText("High")).toBeInTheDocument();
    expect(within(first).getByText(/at or above the 1% threshold/)).toBeInTheDocument();
    expect(within(first).getByRole("button", { name: "Acknowledge: Vale, Ada" })).toBeInTheDocument();
    // An acknowledged flag can only be resolved.
    const second = screen.getAllByRole("row")[2];
    expect(within(second).queryByRole("button", { name: /Acknowledge/ })).toBeNull();
    expect(within(second).getByRole("button", { name: "Resolve: Ash, Bo" })).toBeInTheDocument();
  });

  it("is read-only and pseudonymous for de-identified rows", () => {
    const deid: WorklistRow[] = [{ patient: { ...base, patient_id: "R-00ff", age_band: "50-54" }, score: 10, flags: [flag] }];
    renderWithQuery(<WorklistTable rows={deid} offset={0} canReview={false} />);
    expect(screen.getByRole("link", { name: "R-00ff" })).toBeInTheDocument();
    expect(screen.getByText("Age 50-54 · Kidney")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Acknowledge|Resolve/ })).toBeNull();
  });

  it("sorts by a column header and reports it to assistive tech", async () => {
    renderWithQuery(<WorklistTable rows={identified} offset={0} canReview={false} />);
    await userEvent.click(screen.getByRole("button", { name: /Score/ }));
    const header = screen.getByRole("columnheader", { name: /Score/ });
    expect(header).toHaveAttribute("aria-sort");
    const order = () => screen.getAllByRole("link").map((link) => link.textContent);
    const first = order();
    await userEvent.click(screen.getByRole("button", { name: /Score/ }));
    expect(order()).toEqual([...first].reverse());
  });
});
