"use client";

import type { Data } from "plotly.js";
import { useMemo } from "react";

import { ChartCard, type ValueRow } from "@/components/charts/chart-card";
import { COLORS, Plot } from "@/components/charts/plot";
import { ErrorState, PageSkeleton } from "@/components/states";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ITEM_TYPES, TYPE_LABEL, useReviewMetrics, type ItemType, type ReviewMetrics } from "@/lib/review";

const TYPE_COLORS = [COLORS.primary, COLORS.secondary, COLORS.medium, COLORS.muted];

function percent(value: number | null | undefined): string {
  return value == null ? "—" : `${(value * 100).toFixed(1)}%`;
}

/** Reviews per day as a table (one column per type) and as stacked bars. */
export function perDay(metrics: ReviewMetrics): { rows: ValueRow[]; data: Data[] } {
  const days = [...new Set(metrics.reviewed_per_day.map((d) => d.day))].sort();
  const count = (day: string, type: ItemType) =>
    metrics.reviewed_per_day.find((d) => d.day === day && d.item_type === type)?.reviewed ?? 0;
  const rows = days.map((day) => ({ date: day, values: ITEM_TYPES.map((t) => count(day, t.value)) }));
  const data: Data[] = ITEM_TYPES.map((t, i) => ({
    type: "bar",
    name: t.label,
    x: days,
    y: days.map((day) => count(day, t.value)),
    marker: { color: TYPE_COLORS[i] },
  }));
  return { rows, data };
}

export function MetricsView() {
  const metrics = useReviewMetrics();
  const daily = useMemo(() => (metrics.data ? perDay(metrics.data) : null), [metrics.data]);
  if (metrics.isPending) return <PageSkeleton />;
  if (metrics.isError) return <ErrorState error={metrics.error} onRetry={() => metrics.refetch()} />;
  const m = metrics.data;
  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Review metrics</h1>
        <p className="text-sm text-muted-foreground">Your center&apos;s review queue: what waits, how fast it moves, and how often the model was right.</p>
      </div>

      <ul aria-label="Waiting per type" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {ITEM_TYPES.map((t) => (
          <li key={t.value}>
            <Card>
              <CardHeader>
                <CardDescription>{t.label}</CardDescription>
                <CardTitle className="text-3xl tabular-nums">{m.queue_by_type[t.value] ?? 0}</CardTitle>
              </CardHeader>
              <CardContent className="text-xs text-muted-foreground">waiting</CardContent>
            </Card>
          </li>
        ))}
      </ul>

      {daily && (
        <ChartCard title="Items reviewed per day" description="Last 30 days" columns={ITEM_TYPES.map((t) => t.label)} rows={daily.rows}>
          <Plot data={daily.data} layout={{ barmode: "stack", yaxis: { title: { text: "Reviewed" } } }} label="Items reviewed per day, by type" />
        </ChartCard>
      )}

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>
              <h2>Time to review</h2>
            </CardTitle>
            <CardDescription>Median time from entering the queue to a decision.</CardDescription>
          </CardHeader>
          <CardContent>
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Median time to review by type</caption>
              <thead>
                <tr>
                  <th scope="col" className="py-1 pr-4">Type</th>
                  <th scope="col" className="py-1 pr-4">Reviewed</th>
                  <th scope="col" className="py-1">Median</th>
                </tr>
              </thead>
              <tbody>
                {m.time_to_review.length === 0 && (
                  <tr>
                    <td colSpan={3} className="py-3 text-muted-foreground">No reviews yet.</td>
                  </tr>
                )}
                {m.time_to_review.map((t) => (
                  <tr key={t.item_type} className="border-t">
                    <td className="py-1 pr-4">{TYPE_LABEL[t.item_type as ItemType] ?? t.item_type}</td>
                    <td className="py-1 pr-4 tabular-nums">{t.reviewed}</td>
                    <td className="py-1 tabular-nums">{t.median_hours < 1 ? `${Math.round(t.median_hours * 60)} min` : `${t.median_hours.toFixed(1)} h`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>
              <h2>Reviewer vs model agreement</h2>
            </CardTitle>
            <CardDescription>
              The QC sample was drawn at random from what the model would have auto-approved: its agreement is the honest
              accuracy estimate. Other items came to review because they looked wrong.
            </CardDescription>
          </CardHeader>
          <CardContent className="max-h-96 overflow-auto">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Agreement per field</caption>
              <thead>
                <tr>
                  <th scope="col" className="py-1 pr-3">Field</th>
                  <th scope="col" className="py-1 pr-3">QC sample</th>
                  <th scope="col" className="py-1">Other items</th>
                </tr>
              </thead>
              <tbody>
                {m.agreement.length === 0 && (
                  <tr>
                    <td colSpan={3} className="py-3 text-muted-foreground">No reviews yet.</td>
                  </tr>
                )}
                {m.agreement.map((a) => (
                  <tr key={`${a.item_type}-${a.field}`} className="border-t">
                    <td className="py-1 pr-3">
                      {a.field.replace(/_/g, " ")}
                      <span className="block text-xs text-muted-foreground">{TYPE_LABEL[a.item_type as ItemType] ?? a.item_type}</span>
                    </td>
                    <td className="py-1 pr-3 tabular-nums">
                      {percent(a.qc_agreement)} <span className="text-xs text-muted-foreground">n={a.qc_examples}</span>
                    </td>
                    <td className="py-1 tabular-nums">
                      {percent(a.other_agreement)} <span className="text-xs text-muted-foreground">n={a.other_examples}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
