import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { jsonResponse, renderWithQuery } from "@/test/render";

import { DemoGuide } from "./demo-guide";

afterEach(() => vi.unstubAllGlobals());

describe("DemoGuide", () => {
  it("names the guest accounts, the end date and three things to try, never a password", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(200, { guide: true, guest_until: "2026-12-13T12:00:00Z" })));
    renderWithQuery(<DemoGuide />);
    const guide = await screen.findByRole("heading", { name: "Demo guide" });
    const card = guide.closest("[aria-labelledby]") as HTMLElement;
    expect(card).toHaveTextContent("guest.clinician");
    expect(card).toHaveTextContent("guest.researcher");
    expect(card).toHaveTextContent(/open until 13 Dec 2026/);
    expect(screen.getAllByRole("listitem")).toHaveLength(5);
    expect(card).toHaveTextContent("Password: shared by the author on request.");
  });

  it("shows nothing outside the public demo", async () => {
    const fetch = vi.fn(async () => jsonResponse(200, { guide: false, guest_until: null }));
    vi.stubGlobal("fetch", fetch);
    const { container } = renderWithQuery(<DemoGuide />);
    await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
