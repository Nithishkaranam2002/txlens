import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderWithQuery } from "@/test/render";

import { SuppressedCount } from "./attrition-chart";

describe("SuppressedCount", () => {
  it("explains small-cell suppression", () => {
    renderWithQuery(<SuppressedCount value="<11" />);
    expect(screen.getByTitle("Counts from 1 to 10 are hidden to protect privacy")).toHaveTextContent("<11");
  });

  it("explains a count withheld by the differencing guard", () => {
    renderWithQuery(<SuppressedCount value="withheld" />);
    const count = screen.getByTitle(/with a count you saw earlier it would reveal fewer than 11 patients/);
    expect(count).toHaveTextContent("withheld");
  });
});
