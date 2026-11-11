"use client";

import type { Config, Data, Layout } from "plotly.js";
import { useEffect, useRef } from "react";

export const COLORS = {
  primary: "#0f766e",
  secondary: "#1d4ed8",
  high: "#b91c1c",
  medium: "#b45309",
  muted: "#475569",
  band: "rgba(15, 118, 110, 0.12)",
  grid: "#e2e8f0",
};

const BASE_LAYOUT: Partial<Layout> = {
  autosize: true,
  margin: { l: 56, r: 56, t: 16, b: 48 },
  font: { family: "var(--font-geist-sans), system-ui, sans-serif", size: 12, color: "#1e293b" },
  paper_bgcolor: "rgba(0,0,0,0)",
  plot_bgcolor: "rgba(0,0,0,0)",
  xaxis: { type: "date", gridcolor: COLORS.grid, zeroline: false },
  yaxis: { gridcolor: COLORS.grid, zeroline: false },
  legend: { orientation: "h", y: -0.18 },
  hovermode: "closest",
};
const CONFIG: Partial<Config> = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["lasso2d", "select2d"] };

/**
 * A Plotly chart, loaded only in the browser (Plotly needs `window`) and only on pages that
 * draw charts, so it never weighs down the rest of the app.
 */
export function Plot({ data, layout, label }: { data: Data[]; layout?: Partial<Layout>; label: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = ref.current;
    let plotly: typeof import("plotly.js-basic-dist-min").default | null = null;
    let cancelled = false;
    import("plotly.js-basic-dist-min").then(({ default: loaded }) => {
      if (cancelled || !element) return;
      plotly = loaded;
      const merged = { ...BASE_LAYOUT, ...layout, xaxis: { ...BASE_LAYOUT.xaxis, ...layout?.xaxis }, yaxis: { ...BASE_LAYOUT.yaxis, ...layout?.yaxis } };
      loaded.react(element, data, merged, CONFIG);
    });
    return () => {
      cancelled = true;
      if (plotly && element) plotly.purge(element);
    };
  }, [data, layout]);
  // role="figure", not "img": the chart keeps its own interactive controls (zoom, download).
  return <div ref={ref} role="figure" aria-label={label} className="h-72 w-full" />;
}
