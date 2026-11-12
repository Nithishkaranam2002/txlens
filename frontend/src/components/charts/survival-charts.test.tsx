import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderWithQuery } from "@/test/render";

import { RiskTable } from "./survival-charts";

describe("RiskTable", () => {
  it("shows suppressed cells as <11 with an explanation, never as a number", () => {
    renderWithQuery(
      <RiskTable rows={[{ group: "Living", cells: [{ day: 0, n_risk: 620, events: 0 }, { day: 365, n_risk: 540, events: "<11" }] }]} />,
    );
    const row = screen.getByRole("row", { name: /Living/ });
    expect(within(row).getByText("620")).toBeInTheDocument();
    expect(within(row).getByTitle("Counts from 1 to 10 are hidden to protect privacy")).toHaveTextContent("<11");
    expect(screen.getByRole("columnheader", { name: "12 mo" })).toBeInTheDocument();
  });
});
