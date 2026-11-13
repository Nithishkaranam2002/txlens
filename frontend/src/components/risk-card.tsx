import { ArrowDown, ArrowUp, FlaskConical, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDate } from "@/lib/format";
import type { RiskContribution, RiskScore } from "@/lib/types";
import { cn } from "@/lib/utils";

const YES_NO = new Set(["dsa_positive", "prior_rejection", "organ_heart", "donor_deceased", "dgf"]);

function signed(value: number, digits: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

/** A feature's value in clinical units (the model sees dd-cfDNA as a log). */
export function featureValue({ feature, value }: Pick<RiskContribution, "feature" | "value">): string {
  if (value === null || value === undefined) return "not available";
  if (YES_NO.has(feature)) return value >= 0.5 ? "yes" : "no";
  switch (feature) {
    case "dd_cfdna_log":
      return `${Math.exp(value).toFixed(2)}%`;
    case "dd_cfdna_slope":
      return `${signed(value, 2)}% per month`;
    case "dd_cfdna_vs_min":
      return `${value.toFixed(1)}× own lowest`;
    case "creatinine_change_pct":
      return `${signed(value, 0)}% vs baseline`;
    case "tacrolimus_cv":
      return `CV ${value.toFixed(0)}%`;
    case "months_since_transplant":
      return `${value.toFixed(0)} months`;
    default:
      return value.toFixed(2);
  }
}

/** The feature's name for people (the model's log scale is not shown). */
export function featureLabel({ feature, label }: Pick<RiskContribution, "feature" | "label">): string {
  return feature === "dd_cfdna_log" ? "dd-cfDNA" : label;
}

function percent(value: number): string {
  return `${(100 * value).toFixed(value < 0.1 ? 1 : 0)}%`;
}

const SOURCE_TEXT = { nightly: "nightly run", on_demand: "scored on request" } as const;

/**
 * The patient's rejection-risk score with its top contributing features and the model version.
 * Decision support for review: it is never shown as a diagnosis.
 */
export function RiskCard({
  risk,
  canRescore,
  onRescore,
  rescoring = false,
}: {
  risk: RiskScore;
  canRescore: boolean;
  onRescore?: () => void;
  rescoring?: boolean;
}) {
  const elevated = risk.band === "elevated";
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2">
        <CardTitle>
          <h2>Rejection-risk model</h2>
        </CardTitle>
        {canRescore && (
          <Button variant="outline" size="sm" onClick={onRescore} disabled={rescoring}>
            <RefreshCw aria-hidden className={cn("size-4", rescoring && "animate-spin")} />
            Re-score latest visit
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="flex items-start gap-2 rounded-md border border-dashed bg-muted/40 p-2 text-sm">
          <FlaskConical aria-hidden className="mt-0.5 size-4 shrink-0" />
          <span>{risk.label}</span>
        </p>
        {!risk.available || risk.score == null ? (
          <p className="text-sm text-muted-foreground">
            Not scored yet. The nightly surveillance run scores each patient&apos;s latest scheduled dd-cfDNA visit.
          </p>
        ) : (
          <>
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="text-3xl font-semibold tabular-nums">{percent(risk.score)}</span>
              <span
                className={cn(
                  "rounded-md border px-1.5 py-0.5 text-xs font-semibold",
                  elevated
                    ? "border-severity-high/30 bg-severity-high-bg text-severity-high"
                    : "border-border bg-muted text-muted-foreground",
                )}
              >
                {elevated ? "Elevated model risk" : "Lower model risk"}
              </span>
              <p className="w-full text-sm text-muted-foreground">
                Estimated chance of a biopsy-proven rejection in the 90 days after the visit on{" "}
                <time dateTime={risk.visit_date ?? undefined}>{formatDate(risk.visit_date)}</time>.
                {risk.threshold != null &&
                  ` "Elevated" starts at ${percent(risk.threshold)}, the cut-off that alerts on as many visits as the surveillance rules do.`}
              </p>
            </div>
            <div>
              <h3 className="mb-1 text-sm font-medium">What moved this score most, compared with an average visit</h3>
              <ul className="space-y-1 text-sm" aria-label="Top contributing features">
                {risk.contributions.map((c) => (
                  <li key={c.feature} className="flex items-center gap-2">
                    {c.direction === "raises" ? (
                      <ArrowUp aria-hidden className="size-4 text-severity-high" />
                    ) : (
                      <ArrowDown aria-hidden className="size-4 text-muted-foreground" />
                    )}
                    <span className="font-medium">{featureLabel(c)}</span>
                    <span className="text-muted-foreground">{featureValue(c)}</span>
                    <span className="ml-auto text-xs text-muted-foreground">
                      {c.direction === "raises" ? "raises risk" : "lowers risk"}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
            <p className="text-xs text-muted-foreground">
              Model {risk.model_name} v{risk.model_version} · {risk.source ? SOURCE_TEXT[risk.source] : ""}{" "}
              <time dateTime={risk.scored_at ?? undefined}>{formatDate(risk.scored_at)}</time>. Intended use, data
              and limits: the model card (docs/model_card_risk.md).
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}
