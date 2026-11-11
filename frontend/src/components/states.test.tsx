import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";

import { ErrorState } from "./states";

describe("ErrorState", () => {
  it("shows the message and the request id, and retries", async () => {
    const retry = vi.fn();
    render(<ErrorState error={new ApiError(500, "Internal Server Error", "req-42")} onRetry={retry} />);
    expect(screen.getByText("Internal Server Error")).toBeInTheDocument();
    expect(screen.getByText("req-42")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("says Not found for a 404 (other centers' records look the same) and explains 403", () => {
    const { unmount } = render(<ErrorState error={new ApiError(404, "Patient not found", "r")} />);
    expect(screen.getByRole("heading", { name: "Not found" })).toBeInTheDocument();
    unmount();
    render(<ErrorState error={new ApiError(403, "Your role cannot do this", "r")} />);
    expect(screen.getByRole("heading", { name: "Not available for your role" })).toBeInTheDocument();
  });

  it("explains a network failure without a request id", () => {
    render(<ErrorState error={new TypeError("Failed to fetch")} />);
    expect(screen.getByText("The server could not be reached.")).toBeInTheDocument();
  });
});
