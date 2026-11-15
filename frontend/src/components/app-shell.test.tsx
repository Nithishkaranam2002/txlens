import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { meKey } from "@/lib/auth";
import { renderWithQuery, testMe } from "@/test/render";

import { AppShell } from "./app-shell";

vi.mock("next/navigation", () => ({ usePathname: () => "/worklist", useRouter: () => ({ push: vi.fn() }) }));

describe("AppShell", () => {
  it("tells guest accounts that their changes reset every night", () => {
    const { client } = renderWithQuery(<AppShell>page</AppShell>, "clinician");
    client.setQueryData(meKey, { ...testMe("clinician"), username: "guest.clinician" });
    return vi.waitFor(() =>
      expect(screen.getByRole("note", { name: "Demo account notice" })).toHaveTextContent(
        "Demo account – changes you make reset every night.",
      ),
    );
  });

  it("shows no such notice to a normal account", () => {
    renderWithQuery(<AppShell>page</AppShell>, "clinician");
    expect(screen.getByRole("note", { name: "Data and use notice" })).toBeVisible();
    expect(screen.queryByRole("note", { name: "Demo account notice" })).toBeNull();
  });
});
