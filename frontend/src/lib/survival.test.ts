import { describe, expect, it } from "vitest";

import type { CoxOut, ShownCurve } from "@/lib/types";

import { cleanRequest, forestTraces, percent, stepTraces, toMonths, yRange } from "./survival";

const curve = (group: string, points: [number, number, number | null, number | null][]): ShownCurve => ({
  group, n: 120, events: 30, hidden_reason: null,
  points: points.map(([day, estimate, lower, upper]) => ({ day, estimate, lower, upper })),
});
const living = curve("Living", [[0, 1, null, null], [200, 0.95, 0.92, 0.97], [500, 0.9, 0.86, 0.93]]);
const hidden: ShownCurve = { group: "Heart", n: 40, events: "<11", points: [], hidden_reason: "fewer than 11 events" };

describe("survival chart data", () => {
  it("draws each curve as a step line between its CI band edges", () => {
    const traces = stepTraces([living]);
    expect(traces).toHaveLength(3); // upper edge, lower edge filled to it, estimate
    const [upper, lower, estimate] = traces as { x: number[]; y: number[]; fill?: string; line: { shape: string } }[];
    expect(estimate.line.shape).toBe("hv");
    expect(estimate.x).toEqual([0, 200, 500].map(toMonths));
    expect(lower.fill).toBe("tonexty");
    expect(upper.y[0]).toBe(1); // no interval at the start: the band collapses to the estimate
  });

  it("draws nothing for a hidden group and dashes 1 - KM", () => {
    const traces = stepTraces([hidden], [living]) as { name?: string; line: { dash?: string } }[];
    expect(traces).toHaveLength(1);
    expect(traces[0].name).toBe("Living: 1 − KM");
    expect(traces[0].line.dash).toBe("dash");
  });

  it("zooms survival curves to their band, incidence from 0", () => {
    const [low, high] = yRange([living], 1);
    expect(low).toBeCloseTo(0.81);
    expect(high).toBe(1.01);
    const [zero, top] = yRange([curve("x", [[0, 0, null, null], [9, 0.1, 0.05, 0.2]])], 0);
    expect(zero).toBe(0);
    expect(top).toBeCloseTo(0.22);
  });

  it("lists hazard ratios top-down and marks proportional-hazards problems", () => {
    const term = { key: "dgf", label: "Delayed graft function", hazard_ratio: 2, lower: 1.5, upper: 3,
      p_value: 0.01, ph_p_value: 0.02, ph_violated: true, n_exposed: 50, events_exposed: "<11" };
    const cox: CoxOut = { landmark_days: 30, n: 400, events: 40,
      terms: [{ ...term, key: "age", label: "Age", ph_violated: false }, term] };
    const [trace] = forestTraces(cox) as { y: string[]; error_x: { array: number[]; arrayminus: number[] } }[];
    expect(trace.y).toEqual(["Delayed graft function ⚠", "Age"]);
    expect(trace.error_x.array[0]).toBe(1);
    expect(trace.error_x.arrayminus[0]).toBe(0.5);
  });

  it("formats percentages and drops empty request fields", () => {
    expect(percent(0.1234)).toBe("12.3%");
    expect(percent(null)).toBe("—");
    expect(cleanRequest({ grouping: "organ", covariates: [], cohort_id: null, landmark_days: null }))
      .toEqual({ grouping: "organ", covariates: [] });
  });
});
