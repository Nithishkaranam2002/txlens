"use client";

import { useMemo } from "react";

import { SuppressedCount } from "@/components/charts/attrition-chart";
import { ChartCard, type ValueRow } from "@/components/charts/chart-card";
import { Plot } from "@/components/charts/plot";
import { formatCount } from "@/lib/format";
import { curveLayout, forestLayout, forestTraces, percent, stepTraces, toMonths, yRange } from "@/lib/survival";
import type { CoxOut, RiskRow, ShownCurve } from "@/lib/types";

const months = (value: string) => `${toMonths(Number(value)).toFixed(1)} mo`;

export function Count({ value }: { value: number | string }) {
  return typeof value === "string" ? <SuppressedCount value={value} /> : <>{formatCount(value)}</>;
}

/** Table rows for the "values as a table" view: one row per displayed step and group. */
function curveRows(curves: ShownCurve[]): ValueRow[] {
  return curves.flatMap((c) =>
    c.points.map((p) => ({ date: String(p.day), values: [c.group, percent(p.estimate), `${percent(p.lower)} – ${percent(p.upper)}`] })),
  );
}

function hiddenNote(curves: ShownCurve[]) {
  const hidden = curves.filter((c) => c.hidden_reason);
  if (hidden.length === 0) return null;
  return (
    <p className="text-xs text-muted-foreground">
      Not drawn ({hidden.map((c) => `${c.group}: ${c.hidden_reason}`).join("; ")}).
    </p>
  );
}

export function KmChart({ curves }: { curves: ShownCurve[] }) {
  const data = useMemo(() => stepTraces(curves), [curves]);
  const layout = useMemo(() => curveLayout("Graft survival", yRange(curves, 1)), [curves]);
  return (
    <ChartCard
      title="Death-censored graft survival"
      description="Kaplan-Meier with 95% confidence bands. Each step shows at least 11 graft losses; curves stop before fewer than 11 patients are at risk."
      columns={["Group", "Survival", "95% CI"]}
      rows={curveRows(curves)}
      xLabel="Months since transplant"
      formatX={months}
    >
      <Plot data={data} layout={layout} label={`Kaplan-Meier graft survival for ${curves.map((c) => c.group).join(", ")}`} />
      {hiddenNote(curves)}
    </ChartCard>
  );
}

export function IncidenceChart({ incidence, oneMinusKm }: { incidence: ShownCurve[]; oneMinusKm: ShownCurve[] }) {
  const data = useMemo(() => stepTraces(incidence, oneMinusKm), [incidence, oneMinusKm]);
  const layout = useMemo(() => curveLayout("Cumulative incidence of graft loss", yRange([...incidence, ...oneMinusKm], 0)), [incidence, oneMinusKm]);
  return (
    <ChartCard
      title="Cumulative incidence of graft loss (death as a competing event)"
      description="Aalen-Johansen (solid, with 95% bands) against 1 − Kaplan-Meier (dashed). 1 − KM treats patients who died as if they could still lose the graft, so it overstates the risk."
      columns={["Group", "Cumulative incidence", "95% CI"]}
      rows={curveRows(incidence)}
      xLabel="Months since transplant"
      formatX={months}
    >
      <Plot data={data} layout={layout} label="Cumulative incidence of graft loss compared with one minus Kaplan-Meier" />
      {hiddenNote(incidence)}
    </ChartCard>
  );
}

export function RiskTable({ rows }: { rows: RiskRow[] }) {
  if (rows.length === 0) return null;
  const days = rows[0].cells.map((c) => c.day);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <caption className="pb-2 text-left text-xs text-muted-foreground">
          Number at risk, with graft losses since the previous column in brackets
        </caption>
        <thead className="text-xs text-muted-foreground">
          <tr>
            <th scope="col" className="pb-1 pr-4">Group</th>
            {days.map((day) => (
              <th key={day} scope="col" className="pb-1 pr-4 text-right">{`${Math.round(toMonths(day))} mo`}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.group} className="border-t">
              <th scope="row" className="py-1 pr-4 font-normal">{row.group}</th>
              {row.cells.map((cell) => (
                <td key={cell.day} className="py-1 pr-4 text-right tabular-nums">
                  <Count value={cell.n_risk} /> <span className="text-muted-foreground">(<Count value={cell.events} />)</span>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ForestPlot({ cox }: { cox: CoxOut }) {
  const data = useMemo(() => forestTraces(cox), [cox]);
  const layout = useMemo(() => forestLayout(cox.terms.length), [cox]);
  return (
    <div className="space-y-3">
      <Plot data={data} layout={layout} label={`Hazard ratios: ${cox.terms.map((t) => `${t.label} ${t.hazard_ratio.toFixed(2)}`).join("; ")}`} />
      <table className="w-full text-left text-sm">
        <caption className="sr-only">Cox model hazard ratios</caption>
        <thead className="text-xs text-muted-foreground">
          <tr>
            <th scope="col" className="pb-1">Covariate</th>
            <th scope="col" className="pb-1 text-right">HR (95% CI)</th>
            <th scope="col" className="pb-1 text-right">p</th>
            <th scope="col" className="pb-1 text-right">PH test p</th>
            <th scope="col" className="pb-1 text-right">Patients / losses with it</th>
          </tr>
        </thead>
        <tbody>
          {cox.terms.map((t) => (
            <tr key={t.key} className="border-t">
              <th scope="row" className="py-1 font-normal">{t.label}</th>
              <td className="py-1 text-right tabular-nums">{`${t.hazard_ratio.toFixed(2)} (${t.lower.toFixed(2)}–${t.upper.toFixed(2)})`}</td>
              <td className="py-1 text-right tabular-nums">{t.p_value.toFixed(3)}</td>
              <td className={`py-1 text-right tabular-nums ${t.ph_violated ? "font-semibold text-amber-700" : ""}`}>
                {t.ph_p_value.toFixed(3)}{t.ph_violated && " ⚠"}
              </td>
              <td className="py-1 text-right tabular-nums">
                {t.n_exposed === null || t.n_exposed === undefined ? "—" : <><Count value={t.n_exposed} /> / <Count value={t.events_exposed ?? 0} /></>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
