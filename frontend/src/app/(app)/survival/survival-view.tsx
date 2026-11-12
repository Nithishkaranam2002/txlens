"use client";

import { Copy, Download, Info } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";

import { Count, ForestPlot, IncidenceChart, KmChart, RiskTable } from "@/components/charts/survival-charts";
import { FilterSelect } from "@/components/filter-select";
import { ErrorState } from "@/components/states";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { isApiError } from "@/lib/api/errors";
import { useCohorts, useSurvivalAnalysis, useSurvivalOptions } from "@/lib/queries";
import { downloadReport, percent } from "@/lib/survival";
import type { SurvivalReport, SurvivalRequest } from "@/lib/types";

type Grouping = SurvivalRequest["grouping"];
type Covariate = SurvivalRequest["covariates"][number];
const ALL_PATIENTS = "all";

/** Pick a population and grouping, run the analysis, export it. Synthetic data only. */
export function SurvivalView() {
  const params = useSearchParams();
  const options = useSurvivalOptions();
  const cohorts = useCohorts();
  const [population, setPopulation] = useState(params.get("cohort") ?? ALL_PATIENTS);
  const [grouping, setGrouping] = useState<Grouping>("donor_type");
  const [compare, setCompare] = useState<string | undefined>(undefined);
  const [landmark, setLandmark] = useState("");
  const [covariates, setCovariates] = useState<Covariate[] | null>(null);
  const [submitted, setSubmitted] = useState<SurvivalRequest | null>(null);
  const analysis = useSurvivalAnalysis(submitted);

  if (options.isError) return <ErrorState error={options.error} onRetry={() => options.refetch()} />;
  const groupings = options.data?.groupings ?? [];
  const allCovariates = (options.data?.covariates ?? []).map((c) => c.key as Covariate);
  const chosen = covariates ?? allCovariates;
  const horizon = groupings.find((g) => g.key === grouping)?.horizon_days ?? 0;
  const cohortOptions = (cohorts.data?.items ?? []).map((c) => ({ value: c.cohort_id, label: c.name }));

  const run = (event: FormEvent) => {
    event.preventDefault();
    setSubmitted({
      cohort_id: population === ALL_PATIENTS ? null : population,
      grouping,
      compare_cohort_id: grouping === "cohort" ? (compare ?? null) : null,
      landmark_days: landmark === "" ? null : Number(landmark),
      covariates: chosen,
    });
  };
  const toggle = (key: Covariate, on: boolean) =>
    setCovariates(on ? [...chosen, key] : chosen.filter((c) => c !== key));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Graft survival analysis</h1>
        <p className="text-sm text-muted-foreground">
          Death-censored graft survival, competing risks and a Cox model on synthetic data. Every role sees
          aggregates only: counts from 1 to 10 show as “&lt;11”, and curve steps are pooled so that each
          covers at least 11 graft losses.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle><h2>Analysis</h2></CardTitle>
          <CardDescription>
            Groups known only after transplant (DSA at month 1, dd-cfDNA or rejection in year 1, cohorts with
            lab rules) start at a landmark: follow-up begins when the group is known, among patients still at
            risk, to avoid immortal time bias.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={run} className="space-y-4">
            <div className="flex flex-wrap items-end gap-4">
              <FilterSelect
                id="survival-population" label="Population" width="w-56"
                value={population} allowAll={false}
                options={[{ value: ALL_PATIENTS, label: "All patients" }, ...cohortOptions]}
                onChange={(v) => setPopulation(v ?? ALL_PATIENTS)}
              />
              <FilterSelect
                id="survival-grouping" label="Compare by" width="w-64" value={grouping} allowAll={false}
                options={groupings.map((g) => ({ value: g.key as Grouping, label: g.label }))}
                onChange={(v) => v && setGrouping(v)}
              />
              {grouping === "cohort" && (
                <FilterSelect
                  id="survival-compare" label="Cohort" width="w-56" value={compare} allowAll={false}
                  options={cohortOptions} onChange={setCompare}
                />
              )}
              <div className="space-y-1.5">
                <Label htmlFor="survival-landmark">Landmark (days)</Label>
                <Input
                  id="survival-landmark" type="number" min={0} max={1095} className="w-32 bg-card"
                  placeholder={grouping === "cohort" ? "from rules" : String(horizon)}
                  value={landmark} onChange={(e) => setLandmark(e.target.value)}
                  aria-describedby="survival-landmark-help"
                />
              </div>
            </div>
            <p id="survival-landmark-help" className="text-xs text-muted-foreground">
              {grouping === "cohort"
                ? "Empty: starts when the cohort's rules are known (the end of its latest lab window)."
                : `Empty: day ${horizon}, when this grouping is known. An earlier landmark is refused.`}
            </p>
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium">Cox model covariates</legend>
              <div className="flex flex-wrap gap-4">
                {(options.data?.covariates ?? []).map((c) => (
                  <label key={c.key} className="flex items-center gap-2 text-sm">
                    <Checkbox
                      checked={chosen.includes(c.key as Covariate)}
                      onCheckedChange={(on) => toggle(c.key as Covariate, on === true)}
                    />
                    {c.label}
                  </label>
                ))}
              </div>
            </fieldset>
            <Button type="submit" disabled={options.isPending || (grouping === "cohort" && !compare)}>
              Run analysis
            </Button>
          </form>
        </CardContent>
      </Card>

      {submitted && analysis.isPending && <Skeleton className="h-96 w-full" />}
      {analysis.isError && (
        isApiError(analysis.error, 422) ? (
          <Alert variant="destructive">
            <AlertTitle>This analysis cannot be run</AlertTitle>
            <AlertDescription>{analysis.error.message}</AlertDescription>
          </Alert>
        ) : (
          <ErrorState error={analysis.error} onRetry={() => analysis.refetch()} />
        )
      )}
      {analysis.data && submitted && <Results report={analysis.data} request={submitted} />}
    </div>
  );
}

function Results({ report, request }: { report: SurvivalReport; request: SurvivalRequest }) {
  const [exporting, setExporting] = useState<"csv" | "pdf" | null>(null);
  const exportAs = async (format: "csv" | "pdf") => {
    setExporting(format);
    try {
      await downloadReport(format, request);
    } catch (error) {
      toast.error(isApiError(error) ? error.message : "The export failed.");
    } finally {
      setExporting(null);
    }
  };
  const copyMethods = async () => {
    await navigator.clipboard.writeText(report.methods);
    toast.success("Methods paragraph copied");
  };

  return (
    <section aria-labelledby="results-heading" className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="results-heading" className="text-lg font-semibold">Results · synthetic data</h2>
          <p className="text-sm text-muted-foreground">
            {report.population.charAt(0).toUpperCase() + report.population.slice(1)} · {report.grouping_label} · follow-up from day {report.landmark_days} ·{" "}
            <Count value={report.n} /> patients, <Count value={report.events} /> graft losses
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => exportAs("csv")} disabled={exporting !== null}>
            <Download aria-hidden className="size-4" /> {exporting === "csv" ? "Exporting…" : "CSV"}
          </Button>
          <Button variant="outline" size="sm" onClick={() => exportAs("pdf")} disabled={exporting !== null}>
            <Download aria-hidden className="size-4" /> {exporting === "pdf" ? "Exporting…" : "PDF report"}
          </Button>
        </div>
      </div>

      {report.warnings.length > 0 && (
        <Alert>
          <Info aria-hidden className="size-4" />
          <AlertTitle>Notes</AlertTitle>
          <AlertDescription>
            <ul className="list-disc space-y-0.5 pl-5">{report.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
          </AlertDescription>
        </Alert>
      )}

      <KmChart curves={report.km} />
      <Card>
        <CardHeader><CardTitle><h3>Number at risk</h3></CardTitle></CardHeader>
        <CardContent className="space-y-2">
          <RiskTable rows={report.risk_table} />
          {report.log_rank && (
            <p className="text-sm">
              Log-rank test: χ² = {report.log_rank.chi2.toFixed(2)} on {report.log_rank.df} df,
              p = {report.log_rank.p_value < 0.001 ? "<0.001" : report.log_rank.p_value.toFixed(3)}.
            </p>
          )}
        </CardContent>
      </Card>

      <IncidenceChart incidence={report.incidence} oneMinusKm={report.one_minus_km} />
      {report.incidence_at.length > 0 && (
        <Card>
          <CardHeader><CardTitle><h3>Cumulative incidence vs 1 − KM</h3></CardTitle></CardHeader>
          <CardContent>
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-muted-foreground">
                <tr>
                  <th scope="col" className="pb-1">Group</th>
                  <th scope="col" className="pb-1 text-right">Year</th>
                  <th scope="col" className="pb-1 text-right">Cumulative incidence</th>
                  <th scope="col" className="pb-1 text-right">1 − KM</th>
                  <th scope="col" className="pb-1 text-right">As of curve step</th>
                </tr>
              </thead>
              <tbody>
                {report.incidence_at.map((row) => (
                  <tr key={`${row.group}-${row.day}`} className="border-t">
                    <td className="py-1">{row.group}</td>
                    <td className="py-1 text-right">{Math.round(row.day / 365)}</td>
                    <td className="py-1 text-right tabular-nums">{percent(row.cumulative_incidence)}</td>
                    <td className="py-1 text-right tabular-nums">{percent(row.one_minus_km)}</td>
                    <td className="py-1 text-right text-muted-foreground tabular-nums">
                      {row.step_day === null || row.step_day === undefined ? "—" : `day ${Math.round(row.step_day)}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-xs text-muted-foreground">
              Read off the displayed curves: the value of their last step by then (the day is shown), because
              steps are pooled to at least 11 graft losses. “—”: no step shown yet, or past the curve’s end.
            </p>
          </CardContent>
        </Card>
      )}

      {report.cox && (
        <Card>
          <CardHeader>
            <CardTitle><h3>Hazard ratios (Cox model)</h3></CardTitle>
            <CardDescription>
              From day {report.cox.landmark_days}; <Count value={report.cox.n} /> patients,{" "}
              <Count value={report.cox.events} /> graft losses. ⚠ marks a covariate whose Schoenfeld test
              suggests the proportional-hazards assumption does not hold.
            </CardDescription>
          </CardHeader>
          <CardContent><ForestPlot cox={report.cox} /></CardContent>
        </Card>
      )}

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-2">
          <CardTitle><h3>Methods</h3></CardTitle>
          <Button variant="ghost" size="sm" onClick={copyMethods}>
            <Copy aria-hidden className="size-4" /> Copy
          </Button>
        </CardHeader>
        <CardContent>
          <p className="text-sm leading-relaxed">{report.methods}</p>
        </CardContent>
      </Card>
    </section>
  );
}
