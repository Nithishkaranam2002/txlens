import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { useReviewShortcuts, type ShortcutHandlers } from "./use-review-shortcuts";

function Harness({ handlers }: { handlers: ShortcutHandlers }) {
  useReviewShortcuts(handlers);
  return <input aria-label="note" />;
}

function handlers(): ShortcutHandlers {
  return { approve: vi.fn(), edit: vi.fn(), reject: vi.fn(), next: vi.fn(), previous: vi.fn(), help: vi.fn() };
}

describe("review shortcuts", () => {
  it("maps a e r j k ? to their actions", async () => {
    const h = handlers();
    render(<Harness handlers={h} />);
    await userEvent.keyboard("aerjk?");
    for (const action of ["approve", "edit", "reject", "next", "previous", "help"] as const) {
      expect(h[action]).toHaveBeenCalledTimes(1);
    }
  });

  it("does nothing while typing or with a modifier key", async () => {
    const h = handlers();
    render(<Harness handlers={h} />);
    await userEvent.type(screen.getByRole("textbox", { name: "note" }), "area");
    await userEvent.keyboard("{Control>}a{/Control}");
    expect(h.approve).not.toHaveBeenCalled();
    expect(h.reject).not.toHaveBeenCalled();
  });
});
