import { describe, expect, it } from "vitest";

import { flaggedDates, flaggedPoints, positiveCutoff, seriesByLab, targetBands, thresholdLevels } from "./chart-data";
import type { Flag, SurveillanceConfig } from "./types";

const config = {
  id: "kidney_transplant", version: 1, title: "Kidney", program: "kidney", anchor: "transplant_date",
  markers: {}, schedule: null,
  rules: [
    { id: "t", kind: "threshold", marker: "dd_cfdna", severity: "medium", group: null, max_age_days: 90,
      levels: [{ at_or_above: 0.5, severity: "medium", meaning: "possible graft injury" },
               { at_or_above: 1.0, severity: "high", meaning: "high risk of rejection" }] },
    { id: "tac", kind: "range_by_phase", marker: "tacrolimus_trough", severity: "low", group: null, max_age_days: 60,
      phases: [{ from_month: 0, to_month: 3, low: 8, high: 12 }, { from_month: 3, to_month: null, low: 5, high: 8 }] },
    { id: "dsa", kind: "new_positive", marker: "dsa_mfi", severity: "high", group: null, max_age_days: null,
      positive_at_or_above: 1000, active_days: 90 },
  ],
  severity_weights: {},
} as unknown as SurveillanceConfig;

describe("chart data", () => {
  it("splits labs by marker, oldest first, without empty values", () => {
    const series = seriesByLab([
      { date: "2025-02-01", time: null, lab: "dd_cfdna", value: 0.4, unit: "%" },
      { date: "2025-01-01", time: null, lab: "dd_cfdna", value: 0.2, unit: "%" },
      { date: "2025-01-05", time: null, lab: "egfr", value: null, unit: null },
    ]);
    expect(series).toEqual({ dd_cfdna: { dates: ["2025-01-01", "2025-02-01"], values: [0.2, 0.4] } });
  });

  it("reads threshold lines and the DSA cut-off from the config", () => {
    expect(thresholdLevels(config, "dd_cfdna").map((l) => l.value)).toEqual([0.5, 1.0]);
    expect(thresholdLevels(config, "egfr")).toEqual([]);
    expect(positiveCutoff(config, "dsa_mfi")).toBe(1000);
  });

  it("turns tacrolimus phases into date bands from the transplant date", () => {
    const bands = targetBands(config, "tacrolimus_trough", "2024-01-01", "2025-06-30");
    expect(bands).toEqual([
      { start: "2024-01-01", end: "2024-04-01", low: 8, high: 12, label: "Target 8–12 (months 0–3)" },
      { start: "2024-04-01", end: "2025-06-30", low: 5, high: 8, label: "Target 5–8 (month 3+)" },
    ]);
  });

  it("marks the results that triggered a flag", () => {
    const flag = { evidence: [
      { marker: "dd_cfdna", date: "2025-02-01", value: 1.2, role: "latest" },
      { marker: "dd_cfdna", date: "2025-01-01", value: 0.2, role: "baseline" },
    ] } as Flag;
    const dates = flaggedDates([flag], "dd_cfdna");
    expect([...dates]).toEqual(["2025-02-01"]);
    expect(flaggedPoints({ dates: ["2025-01-01", "2025-02-01"], values: [0.2, 1.2] }, dates)).toEqual({
      dates: ["2025-02-01"], values: [1.2],
    });
  });
});
