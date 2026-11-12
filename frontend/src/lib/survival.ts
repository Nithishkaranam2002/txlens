import type { Data, Layout } from "plotly.js";

import { api, unwrap } from "@/lib/api/client";
import { DAYS_PER_MONTH } from "@/lib/chart-data";
import type { CoxOut, ShownCurve, SurvivalRequest } from "@/lib/types";

/** One colour per group, in order (the app's chart palette). */
export const GROUP_COLORS = ["#0f766e", "#1d4ed8", "#b45309", "#b91c1c", "#475569"];

export const toMonths = (day: number) => day / DAYS_PER_MONTH;

/** "rgba(...)" at 15% opacity for a group's confidence band. */
function band(hex: string): string {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  return `rgba(${r}, ${g}, ${b}, 0.15)`;
}

/**
 * Step curves with 95% CI bands. The API already pooled the steps (each drop >= 11 events)
 * and stopped each curve before fewer than 11 patients were at risk; nothing is added here.
 * `dashed` curves (1 - KM) are drawn for comparison without bands.
 */
export function stepTraces(curves: ShownCurve[], dashed: ShownCurve[] = []): Data[] {
  const traces: Data[] = [];
  curves.forEach((curve, i) => {
    if (curve.points.length === 0) return;
    const color = GROUP_COLORS[i % GROUP_COLORS.length];
    const x = curve.points.map((p) => toMonths(p.day));
    const estimate = curve.points.map((p) => p.estimate);
    const upper = curve.points.map((p) => p.upper ?? p.estimate);
    const lower = curve.points.map((p) => p.lower ?? p.estimate);
    const line = { shape: "hv" as const, width: 0 };
    traces.push(
      { x, y: upper, type: "scatter", mode: "lines", line, hoverinfo: "skip", showlegend: false, legendgroup: curve.group },
      {
        x, y: lower, type: "scatter", mode: "lines", line, fill: "tonexty", fillcolor: band(color),
        hoverinfo: "skip", showlegend: false, legendgroup: curve.group,
      },
      {
        x, y: estimate, type: "scatter", mode: "lines", name: curve.group, legendgroup: curve.group,
        line: { shape: "hv", color, width: 2 },
        hovertemplate: `${curve.group}<br>month %{x:.1f}: %{y:.3f}<extra></extra>`,
      },
    );
  });
  dashed.forEach((curve, i) => {
    if (curve.points.length === 0) return;
    traces.push({
      x: curve.points.map((p) => toMonths(p.day)), y: curve.points.map((p) => p.estimate),
      type: "scatter", mode: "lines", name: `${curve.group}: 1 − KM`,
      line: { shape: "hv", dash: "dash", width: 1.5, color: GROUP_COLORS[i % GROUP_COLORS.length] },
      hovertemplate: `${curve.group} 1 − KM<br>month %{x:.1f}: %{y:.3f}<extra></extra>`,
    });
  });
  return traces;
}

/** Y range that shows the curves' detail: [lowest band - margin, top]. */
export function yRange(curves: ShownCurve[], top: number): [number, number] {
  const values = curves.flatMap((c) => c.points.flatMap((p) => [p.lower ?? p.estimate, p.upper ?? p.estimate]));
  if (top === 1) {
    const low = Math.min(1, ...values);
    return [Math.max(0, low - 0.05), 1.01];
  }
  return [0, Math.min(1, Math.max(0.05, ...values) + 0.02)];
}

export const curveLayout = (yTitle: string, range: [number, number]): Partial<Layout> => ({
  xaxis: { type: "linear", title: { text: "Months since transplant" }, rangemode: "tozero" },
  yaxis: { title: { text: yTitle }, range, tickformat: ".0%" },
});

/** Hazard ratios with 95% CIs on a log axis; covariates that break proportional hazards
 * are marked. */
export function forestTraces(cox: CoxOut): Data[] {
  const terms = [...cox.terms].reverse(); // Plotly draws the first category at the bottom
  return [
    {
      type: "scatter", mode: "markers", x: terms.map((t) => t.hazard_ratio),
      y: terms.map((t) => (t.ph_violated ? `${t.label} ⚠` : t.label)),
      error_x: {
        type: "data", symmetric: false, array: terms.map((t) => t.upper - t.hazard_ratio),
        arrayminus: terms.map((t) => t.hazard_ratio - t.lower), color: "#1e293b",
      },
      marker: { color: terms.map((t) => (t.ph_violated ? "#b45309" : "#0f766e")), size: 9 },
      hovertemplate: "%{y}: HR %{x:.2f}<extra></extra>",
    } as Data,
  ];
}

export const forestLayout = (rows: number): Partial<Layout> => ({
  xaxis: {
    type: "log", title: { text: "Hazard ratio (95% CI), log scale" },
    tickvals: [0.1, 0.2, 0.5, 1, 2, 5, 10, 20], ticktext: ["0.1", "0.2", "0.5", "1", "2", "5", "10", "20"],
  },
  yaxis: { automargin: true },
  shapes: [{ type: "line", x0: 1, x1: 1, yref: "paper", y0: 0, y1: 1, line: { dash: "dot", color: "#475569" } }],
  showlegend: false,
  height: 120 + rows * 40,
  margin: { l: 8, r: 24, t: 8, b: 48 },
});

export const percent = (value: number | null | undefined) =>
  value === null || value === undefined ? "—" : `${(value * 100).toFixed(1)}%`;

/** The request as the API wants it (no empty optional fields). */
export function cleanRequest(request: SurvivalRequest): SurvivalRequest {
  return Object.fromEntries(
    Object.entries(request).filter(([, v]) => v !== null && v !== undefined && v !== ""),
  ) as SurvivalRequest;
}

/** Download the analysis as CSV or PDF (built by the API from the suppressed report). */
export async function downloadReport(format: "csv" | "pdf", request: SurvivalRequest): Promise<void> {
  const body = cleanRequest(request);
  const result =
    format === "csv"
      ? await api.POST("/survival/report.csv", { body, parseAs: "blob" })
      : await api.POST("/survival/report.pdf", { body, parseAs: "blob" });
  const blob = unwrap(result) as unknown as Blob;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `graft-survival-synthetic.${format}`;
  link.click();
  URL.revokeObjectURL(url);
}
