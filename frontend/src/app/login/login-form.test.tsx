import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";
import { jsonResponse, renderWithQuery } from "@/test/render";

import { LoginForm, loginErrorMessage } from "./login-form";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  useSearchParams: () => new URLSearchParams("next=/patients/R-abc"),
}));

afterEach(() => {
  vi.unstubAllGlobals();
  replace.mockReset();
});

describe("loginErrorMessage", () => {
  it("explains rate limiting with the wait time", () => {
    expect(loginErrorMessage(new ApiError(429, "x", "r", 900))).toBe(
      "Too many failed sign-in attempts. Try again in 15 minutes.",
    );
  });

  it("gives one message for wrong user or password, and a network message", () => {
    expect(loginErrorMessage(new ApiError(401, "x", "r"))).toBe("Invalid user name or password.");
    expect(loginErrorMessage(new TypeError("fetch failed"))).toMatch(/Cannot reach the server/);
  });
});

describe("LoginForm", () => {
  async function fillAndSubmit() {
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("User name"), "clinician.center_a");
    await user.type(screen.getByLabelText("Password"), "secret-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
  }

  it("shows the API's refusal in an alert", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(429, { detail: "Too many" }, { "Retry-After": "900" })));
    renderWithQuery(<LoginForm />);
    await fillAndSubmit();
    expect(await screen.findByRole("alert")).toHaveTextContent("Try again in 15 minutes");
  });

  it("goes to the requested page after signing in", async () => {
    const me = { user_id: "u", username: "c", display_name: "C", role: "clinician", tenant_id: "a", tenant_name: "A" };
    vi.stubGlobal("fetch", vi.fn(async (request: Request) =>
      new URL(request.url).pathname === "/api/me" ? jsonResponse(200, me) : jsonResponse(200, {}),
    ));
    renderWithQuery(<LoginForm />);
    await fillAndSubmit();
    await vi.waitFor(() => expect(replace).toHaveBeenCalledWith("/patients/R-abc"));
  });
});
