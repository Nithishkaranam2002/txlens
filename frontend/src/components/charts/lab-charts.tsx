"use client";

import type { Data, Layout, Shape } from "plotly.js";
import { useMemo } from "react";

import { ChartCard, type ValueRow } from "@/components/charts/chart-card";
import { COLORS, Plot } from "@/components/charts/plot";
import {
  flaggedDates,
  flaggedPoints,
  positiveCutoff,
  targetBands,
  thresholdLevels,
  type Series,
} from "@/lib/chart-data";
import type { Flag, SurveillanceConfig } from "@/lib/types";

type Props = { series: Record<string, Series>; config: SurveillanceConfig | null; flags: Flag[] };

const EMPTY: Series = { dates: [], values: [] };

function line(series: Series, name: string, color: string, extra: Partial<Data> = {}): Data {
  return {
    type: "scatter", mode: "lines+markers", name, x: series.dates, y: series.values,
    line: { color, width: 2 }, marker: { color, size: 6 }, ...extra,
  } as Data;
}

/** Flagged results: a larger diamond with an outline, so they stand out without colour. */
function flagged(series: Series, name = "Flagged result"): Data {
  return {
    type: "scatter", mode: "markers", name, x: series.dates, y: series.values,
    marker: { symbol: "diamond", size: 13, color: COLORS.high, line: { color: "#fff", width: 1.5 } },
  } as Data;
}

function hline(value: number, color: string, dash: "dash" | "dot" = "dash"): Partial<Shape> {
  return { type: "line", xref: "paper", x0: 0, x1: 1, yref: "y", y0: value, y1: value, line: { color, width: 1.5, dash } };
}

function rows(series: Series): ValueRow[] {
  return series.dates.map((date, i) => ({ date, values: [series.values[i]] }));
}

export function DdcfdnaChart({ series, config, flags }: Props) {
  const data = series.dd_cfdna ?? EMPTY;
  const { traces, layout, levelText } = useMemo(() => {
    const levels = config ? thresholdLevels(config, "dd_cfdna") : [];
    const marked = flaggedPoints(data, flaggedDates(flags, "dd_cfdna"));
    const traces: Data[] = [line(data, "dd-cfDNA (%)", COLORS.primary)];
    if (marked.dates.length) traces.push(flagged(marked));
    const layout: Partial<Layout> = {
      yaxis: { title: { text: "dd-cfDNA (%)" }, rangemode: "tozero" },
      shapes: levels.map((l) => hline(l.value, l.severity === "high" ? COLORS.high : COLORS.medium)),
      annotations: levels.map((l) => ({
        xref: "paper", x: 1, xanchor: "right", y: l.value, yanchor: "bottom", showarrow: false,
        text: l.label, font: { size: 11, color: l.severity === "high" ? COLORS.high : COLORS.medium },
      })),
    };
    return { traces, layout, levelText: levels.map((l) => l.label).join(", ") };
  }, [data, config, flags]);
  return (
    <ChartCard
      title="dd-cfDNA"
      description={levelText ? `Threshold lines: ${levelText}. Diamonds mark flagged results.` : undefined}
      columns={["dd-cfDNA (%)"]}
      rows={rows(data)}
    >
      <Plot data={traces} layout={layout} label={`dd-cfDNA over time, ${data.dates.length} results. ${levelText}`} />
    </ChartCard>
  );
}

export function KidneyFunctionChart({ series, flags }: Props) {
  const creatinine = series.creatinine ?? EMPTY;
  const egfr = series.egfr ?? EMPTY;
  const { traces, layout } = useMemo(() => {
    const traces: Data[] = [
      line(creatinine, "Creatinine (mg/dL)", COLORS.primary),
      line(egfr, "eGFR (mL/min/1.73m²)", COLORS.secondary, { yaxis: "y2", line: { color: COLORS.secondary, width: 2, dash: "dot" }, marker: { color: COLORS.secondary, size: 6, symbol: "square" } } as Partial<Data>),
    ];
    const markedCreat = flaggedPoints(creatinine, flaggedDates(flags, "creatinine"));
    const markedEgfr = flaggedPoints(egfr, flaggedDates(flags, "egfr"));
    if (markedCreat.dates.length) traces.push(flagged(markedCreat, "Flagged creatinine"));
    if (markedEgfr.dates.length) traces.push({ ...flagged(markedEgfr, "Flagged eGFR"), yaxis: "y2" } as Data);
    const layout: Partial<Layout> = {
      yaxis: { title: { text: "Creatinine (mg/dL)" }, rangemode: "tozero" },
      yaxis2: { title: { text: "eGFR" }, overlaying: "y", side: "right", rangemode: "tozero", showgrid: false },
    };
    return { traces, layout };
  }, [creatinine, egfr, flags]);
  const byDate = new Map<string, [number | null, number | null]>();
  creatinine.dates.forEach((d, i) => byDate.set(d, [creatinine.values[i], byDate.get(d)?.[1] ?? null]));
  egfr.dates.forEach((d, i) => byDate.set(d, [byDate.get(d)?.[0] ?? null, egfr.values[i]]));
  const table = [...byDate.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([date, values]) => ({ date, values }));
  return (
    <ChartCard title="Creatinine and eGFR" description="Graft function. Solid line: creatinine (left axis); dotted: eGFR (right axis)." columns={["Creatinine (mg/dL)", "eGFR"]} rows={table}>
      <Plot data={traces} layout={layout} label={`Creatinine and eGFR over time, ${table.length} dates`} />
    </ChartCard>
  );
}

export function TacrolimusChart({ series, config, flags, transplantDate }: Props & { transplantDate: string | null }) {
  const data = series.tacrolimus_trough ?? EMPTY;
  const { traces, layout, bandText } = useMemo(() => {
    const last = data.dates.at(-1) ?? transplantDate ?? "";
    const bands = config && transplantDate ? targetBands(config, "tacrolimus_trough", transplantDate, last) : [];
    const marked = flaggedPoints(data, flaggedDates(flags, "tacrolimus_trough"));
    const traces: Data[] = [line(data, "Tacrolimus trough (ng/mL)", COLORS.primary)];
    if (marked.dates.length) traces.push(flagged(marked));
    const layout: Partial<Layout> = {
      yaxis: { title: { text: "ng/mL" }, rangemode: "tozero" },
      shapes: bands.map((b) => ({
        type: "rect", xref: "x", yref: "y", x0: b.start, x1: b.end, y0: b.low, y1: b.high,
        fillcolor: COLORS.band, line: { width: 0 }, layer: "below",
      })),
    };
    return { traces, layout, bandText: bands.map((b) => b.label).join("; ") };
  }, [data, config, flags, transplantDate]);
  return (
    <ChartCard
      title="Tacrolimus trough"
      description={bandText ? `Shaded: target range by phase after transplant (${bandText}).` : undefined}
      columns={["Tacrolimus (ng/mL)"]}
      rows={rows(data)}
    >
      <Plot data={traces} layout={layout} label={`Tacrolimus trough over time, ${data.dates.length} results. ${bandText}`} />
    </ChartCard>
  );
}

export function DsaChart({ series, config, flags }: Props) {
  const data = series.dsa_mfi ?? EMPTY;
  const { traces, layout, cutoff } = useMemo(() => {
    const cutoff = config ? positiveCutoff(config, "dsa_mfi") : null;
    const marked = flaggedPoints(data, flaggedDates(flags, "dsa_mfi"));
    const traces: Data[] = [line(data, "DSA max MFI", COLORS.primary)];
    if (marked.dates.length) traces.push(flagged(marked));
    const layout: Partial<Layout> = {
      yaxis: { title: { text: "MFI" }, rangemode: "tozero" },
      shapes: cutoff ? [hline(cutoff, COLORS.high)] : [],
      annotations: cutoff
        ? [{ xref: "paper", x: 1, xanchor: "right", y: cutoff, yanchor: "bottom", showarrow: false, text: `Positive ≥ ${cutoff} MFI`, font: { size: 11, color: COLORS.high } }]
        : [],
    };
    return { traces, layout, cutoff };
  }, [data, config, flags]);
  return (
    <ChartCard
      title="Donor-specific antibodies (DSA)"
      description={cutoff ? `Dashed line: positive at or above ${cutoff} MFI.` : undefined}
      columns={["Max MFI"]}
      rows={rows(data)}
    >
      <Plot data={traces} layout={layout} label={`DSA maximum MFI over time, ${data.dates.length} results`} />
    </ChartCard>
  );
}
