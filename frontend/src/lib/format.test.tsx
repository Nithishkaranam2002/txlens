import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SuppressedCount } from "@/components/charts/attrition-chart";

import { countValue, formatCount, formatDate, patientName } from "./format";

describe("format", () => {
  it("formats ISO dates without time-zone drift", () => {
    expect(formatDate("2025-03-01")).toBe("1 Mar 2025");
    expect(formatDate("2025-12-31T23:30:00+00:00")).toBe("31 Dec 2025");
    expect(formatDate(null)).toBe("—");
  });

  it("reads small-cell suppressed counts as text and never as zero", () => {
    expect(formatCount(1234)).toBe("1,234");
    expect(formatCount("<11")).toBe("fewer than 11");
    expect(countValue("<11")).toBeNull();
  });

  it("explains a suppressed count to screen readers and on hover", () => {
    render(<SuppressedCount value="<11" />);
    const abbr = screen.getByTitle("Counts from 1 to 10 are hidden to protect privacy");
    expect(abbr).toHaveTextContent("<11 (fewer than 11, hidden to protect privacy)");
  });

  it("uses the pseudonym as the name for de-identified patients", () => {
    expect(patientName({ patient_id: "R-ab", age_band: "40-44", sex: null, organ: null, donor_type: null,
      status: "active", transplant_date: null, death_date: null })).toBe("R-ab");
  });
});
