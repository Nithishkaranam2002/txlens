import type { ReactNode } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDate } from "@/lib/format";

export type ValueRow = { date: string; values: (number | string | null)[] };

/** A chart with a title and, for keyboard and screen-reader users, the same values as a table. */
export function ChartCard({
  title,
  description,
  columns,
  rows,
  children,
  xLabel = "Date",
  formatX = formatDate,
}: {
  title: string;
  description?: string;
  columns: string[];
  rows: ValueRow[];
  children: ReactNode;
  /** The first column: dates by default, or e.g. months since transplant. */
  xLabel?: string;
  formatX?: (value: string) => string;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h3>{title}</h3>
        </CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
      </CardHeader>
      <CardContent className="space-y-2">
        {rows.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">No results recorded.</p>
        ) : (
          <>
            {children}
            <details className="text-sm">
              <summary className="cursor-pointer text-muted-foreground">Show values as a table</summary>
              <div className="mt-2 max-h-64 overflow-auto">
                <table className="w-full text-left text-xs">
                  <caption className="sr-only">{title} values</caption>
                  <thead>
                    <tr>
                      <th scope="col" className="py-1 pr-4">{xLabel}</th>
                      {columns.map((column) => (
                        <th key={column} scope="col" className="py-1 pr-4">{column}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row, i) => (
                      <tr key={`${row.date}-${i}`} className="border-t">
                        <td className="py-1 pr-4">{formatX(row.date)}</td>
                        {row.values.map((value, j) => (
                          <td key={j} className="py-1 pr-4 tabular-nums">{value ?? "—"}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          </>
        )}
      </CardContent>
    </Card>
  );
}
