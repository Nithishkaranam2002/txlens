import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SuppressedCount } from "@/components/charts/attrition-chart";

import { countValue, formatCount, formatDate, formatDateTime, formatUpdated, patientName } from "./format";

describe("format", () => {
  it("shows calendar dates as they are and timestamps in the viewer's time zone", () => {
    const zone = process.env.TZ;
    try {
      process.env.TZ = "America/Chicago";
      expect(formatDate("2025-03-01")).toBe("1 Mar 2025"); // a calendar date never shifts
      // 14 Nov, 7:30 PM in US Central is 15 Nov in UTC: the viewer sees 14 Nov.
      expect(formatDate("2026-11-15T01:30:00Z")).toBe("14 Nov 2026");
      expect(formatDateTime("2026-11-15T01:30:00.123456Z")).toBe("14 Nov 2026, 7:30 PM CST");
      expect(formatDateTime("2025-03-01")).toBe("1 Mar 2025");
      process.env.TZ = "Asia/Tokyo";
      expect(formatDate("2026-11-15T01:30:00+00:00")).toBe("15 Nov 2026");
      expect(formatDate("2025-03-01")).toBe("1 Mar 2025");
    } finally {
      process.env.TZ = zone;
    }
    expect(formatDate(null)).toBe("—");
    expect(formatDate("not a date")).toBe("not a date");
  });

  it("says how long ago a list was updated", () => {
    const now = Date.UTC(2026, 10, 15, 12, 0);
    expect(formatUpdated(now - 20_000, now)).toBe("Updated just now");
    expect(formatUpdated(now - 4 * 60_000, now)).toBe("Updated 4 min ago");
    expect(formatUpdated(now - 130 * 60_000, now)).toBe("Updated 2 h ago");
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
