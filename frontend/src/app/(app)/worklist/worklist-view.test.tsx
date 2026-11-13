import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { WorklistPage, WorklistRow } from "@/lib/types";
import { jsonResponse, renderWithQuery } from "@/test/render";

import { WorklistView } from "./worklist-view";

let search = "";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
  usePathname: () => "/worklist",
  useSearchParams: () => new URLSearchParams(search),
}));

afterEach(() => {
  vi.unstubAllGlobals();
  search = "";
});

const flag = {
  flag_id: "f", rule_id: "r", config_id: "kidney_transplant", config_version: 1, severity: "high" as const,
  reason: "dd-cfDNA above threshold", evidence: [], as_of: "2026-09-30", status: "new" as const,
  status_changed_at: null, status_changed_by: null, note: null, cleared: false,
};
function row(id: number, severity: "high" | "low"): WorklistRow {
  return {
    patient: { patient_id: id, mrn: `MRN-${id}`, given_name: "Pat", family_name: `P${id}`, birth_date: "1970-01-01",
      sex: "female", organ: "kidney", donor_type: "living", status: "active", transplant_date: "2024-01-10",
      death_date: null },
    score: severity === "high" ? 10 : 1,
    flags: [{ ...flag, flag_id: `f${id}`, severity, reason: `${severity} flag of ${id}` }],
  };
}
const page = (items: WorklistRow[]): WorklistPage =>
  ({ items, total: items.length, limit: 50, offset: 0, data_as_of: "2026-09-30" });

/** The API answers by the requested severities: low-only rows for ["low"], one high row otherwise. */
function stubApi() {
  const requests: URL[] = [];
  vi.stubGlobal("fetch", vi.fn(async (request: Request) => {
    const url = new URL(request.url);
    requests.push(url);
    const severities = url.searchParams.getAll("severity");
    return jsonResponse(200, page(severities.join() === "low" ? [row(2, "low"), row(3, "low")] : [row(1, "high")]));
  }));
  return requests;
}

describe("WorklistView", () => {
  it("shows the data date once, defaults to high + medium and collapses low-only patients", async () => {
    const requests = stubApi();
    renderWithQuery(<WorklistView />, "clinician");

    expect(await screen.findByText("high flag of 1")).toBeVisible();
    const asOf = screen.getByText("30 Sep 2026");
    expect(asOf.tagName).toBe("TIME");
    expect(asOf).toHaveAttribute("dateTime", "2026-09-30");
    expect(screen.getAllByText(/as of/i)).toHaveLength(1); // not repeated on each flag
    const asked = requests.map((url) => url.searchParams.getAll("severity").join());
    expect(asked).toContain("high,medium");

    const low = await screen.findByText("Low priority (2 patients)");
    const details = low.closest("details")!;
    expect(details).not.toHaveAttribute("open");
    expect(within(details).getByText("low flag of 2")).not.toBeVisible();
    await userEvent.click(low);
    expect(details).toHaveAttribute("open");
    expect(within(details).getByText("low flag of 2")).toBeVisible();
  });

  it("has no low section when another severity is chosen", async () => {
    search = "severity=all";
    const requests = stubApi();
    renderWithQuery(<WorklistView />, "clinician");
    expect(await screen.findByText("high flag of 1")).toBeVisible();
    expect(screen.queryByText(/Low priority/)).toBeNull();
    expect(requests.every((url) => url.searchParams.getAll("severity").length === 0)).toBe(true);
  });
});
