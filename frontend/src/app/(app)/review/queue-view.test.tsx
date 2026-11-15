import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { QueuePage, ReviewItem } from "@/lib/review";
import { jsonResponse, renderWithQuery } from "@/test/render";

import { QueueView } from "./queue-view";

let search = "";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
  usePathname: () => "/review",
  useSearchParams: () => new URLSearchParams(search),
}));

afterEach(() => {
  vi.unstubAllGlobals();
  search = "";
});

const item: ReviewItem = {
  item_type: "curated_mapping", item_key: "TXLENS_NOTE_ABBREV:abmr", code: "TXLENS_NOTE_ABBREV:abmr",
  title: "Note abbreviation: “abmr” → Antibody-mediated rejection", detail: "AI-curated, pending",
  patient_id: null, queued_at: "2026-11-15T01:30:00Z", tier: 0, tier_label: "rejection / high severity",
  rejection_related: true, high_severity: false, qc_sample: false, status: "approve", decided_at: "2026-11-15T02:00:00Z",
};
const page: QueuePage = { items: [item], total: 1, pending_by_type: { curated_mapping: 3 } };

function stubApi() {
  const requests: URL[] = [];
  vi.stubGlobal("fetch", vi.fn(async (request: Request) => {
    requests.push(new URL(request.url));
    return jsonResponse(200, page);
  }));
  return requests;
}

describe("QueueView", () => {
  it("refreshes the list with the same filters and says when it was updated", async () => {
    search = "type=curated_mapping&status=decided";
    const requests = stubApi();
    renderWithQuery(<QueueView />, "clinician");
    expect(await screen.findByText("Updated just now")).toBeVisible();

    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(requests).toHaveLength(2));
    const asked = requests.map((url) => url.search);
    expect(asked[1]).toBe(asked[0]);
    expect(requests[1].searchParams.get("type")).toBe("curated_mapping");
    expect(requests[1].searchParams.get("status")).toBe("decided");
    expect(await screen.findByText("Updated just now")).toBeVisible();
  });

  it("shows friendly titles and statuses, the raw code on focus, and explains the statuses", async () => {
    stubApi();
    // jsdom has no ResizeObserver; the tooltip's positioning needs one.
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
    renderWithQuery(<QueueView />, "clinician");
    const title = await screen.findByRole("link", { name: "Note abbreviation: “abmr” → Antibody-mediated rejection" });
    expect(screen.queryByText(/^TXLENS_/)).toBeNull();
    expect(screen.getByText("Approved")).toBeInTheDocument();

    title.focus();
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Code: TXLENS_NOTE_ABBREV:abmr");

    screen.getByRole("button", { name: "What the statuses mean" }).focus();
    await waitFor(() => expect(screen.getByRole("tooltip")).toHaveTextContent("Waiting: not checked by a person yet"));
    expect(screen.getByRole("tooltip")).toHaveTextContent(/Approved.*Corrected.*Rejected/);
  });
});
