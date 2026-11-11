"use client";

import type { Data, Layout } from "plotly.js";
import { useMemo } from "react";

import { COLORS, Plot } from "@/components/charts/plot";
import { countValue, formatCount } from "@/lib/format";
import type { Preview } from "@/lib/types";

/**
 * Attrition: how many patients remain after each rule. Suppressed counts ("<11") are drawn as
 * a short hatched stub labelled "<11", never as 0, so no small number can be read off the bar.
 */
export function AttritionChart({ preview }: { preview: Preview }) {
  const { data, layout } = useMemo(() => {
    const steps = [...preview.steps].reverse(); // Plotly draws the first category at the bottom
    const max = Math.max(1, ...steps.map((s) => countValue(s.count) ?? 0));
    const stub = max * 0.02;
    const data: Data[] = [
      {
        type: "bar",
        orientation: "h",
        y: steps.map((s) => s.rule),
        x: steps.map((s) => countValue(s.count) ?? stub),
        text: steps.map((s) => String(s.count)),
        textposition: "outside",
        cliponaxis: false,
        marker: {
          color: steps.map((s) => (countValue(s.count) === null ? COLORS.muted : COLORS.primary)),
          pattern: { shape: steps.map((s) => (countValue(s.count) === null ? "/" : "")) },
        },
        hovertemplate: "%{y}: %{text}<extra></extra>",
      } as Data,
    ];
    const layout: Partial<Layout> = {
      margin: { l: 8, r: 56, t: 8, b: 40 },
      xaxis: { type: "linear", title: { text: "Patients" }, rangemode: "tozero" },
      yaxis: { automargin: true },
      showlegend: false,
      height: 64 + steps.length * 44,
    };
    return { data, layout };
  }, [preview]);

  return (
    <div className="space-y-3">
      <Plot data={data} layout={layout} label={`Attrition: ${preview.steps.map((s) => `${s.rule}, ${formatCount(s.count)}`).join("; ")}`} />
      <table className="w-full text-left text-sm">
        <caption className="sr-only">Patients remaining after each rule</caption>
        <thead className="text-xs text-muted-foreground">
          <tr>
            <th scope="col" className="pb-1">Step</th>
            <th scope="col" className="pb-1 text-right">Patients remaining</th>
          </tr>
        </thead>
        <tbody>
          {preview.steps.map((step, i) => (
            <tr key={i} className="border-t">
              <td className="py-1">{step.rule}</td>
              <td className="py-1 text-right tabular-nums">
                {typeof step.count === "string" ? <SuppressedCount value={step.count} /> : formatCount(step.count)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** "<11" with an explanation (small-cell suppression protects privacy). */
export function SuppressedCount({ value }: { value: string }) {
  return (
    <abbr title="Counts from 1 to 10 are hidden to protect privacy" className="cursor-help no-underline">
      {value}
      <span className="sr-only"> ({formatCount(value)}, hidden to protect privacy)</span>
    </abbr>
  );
}
