"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { AttritionChart, SuppressedCount } from "@/components/charts/attrition-chart";
import { CohortFormFields } from "@/components/cohort-form";
import { EmptyState, ErrorState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useRole } from "@/lib/auth";
import { buildRules, EMPTY_FORM, labelStep, type CohortForm } from "@/lib/cohort-form";
import { formatCount, formatDate } from "@/lib/format";
import { useCohorts, usePreviewCohort, useSaveCohort } from "@/lib/queries";
import { seesIdentifiers } from "@/lib/roles";
import type { CohortRules } from "@/lib/types";

function Count({ value }: { value: number | string }) {
  return typeof value === "string" ? <SuppressedCount value={value} /> : <>{formatCount(value)}</>;
}

export function CohortBuilder() {
  const role = useRole();
  const [form, setForm] = useState<CohortForm>(EMPTY_FORM);
  const [previewed, setPreviewed] = useState<{ rules: CohortRules; form: CohortForm } | null>(null);
  const [name, setName] = useState("");
  const preview = usePreviewCohort();
  const save = useSaveCohort();

  const runPreview = () => {
    const rules = buildRules(form);
    setPreviewed({ rules, form });
    save.reset();
    preview.mutate(rules);
  };
  const saveCohort = (event: FormEvent) => {
    event.preventDefault();
    if (previewed && name.trim()) save.mutate({ name: name.trim(), rules: previewed.rules });
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Cohort builder</h1>
        <p className="text-sm text-muted-foreground">
          Define patients by rules, see how many remain after each one, and save the cohort.
          {!seesIdentifiers(role) && " Counts from 1 to 10 show as “<11” to protect privacy."}
        </p>
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Card>
          <CardHeader>
            <CardTitle><h2>Rules</h2></CardTitle>
          </CardHeader>
          <CardContent>
            <CohortFormFields form={form} onChange={setForm} onSubmit={runPreview} pending={preview.isPending}
              wholeYearsOnly={!seesIdentifiers(role)} />
          </CardContent>
        </Card>

        <Card aria-live="polite">
          <CardHeader>
            <CardTitle><h2>Attrition</h2></CardTitle>
            <CardDescription>Patients remaining after each rule.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {preview.isIdle && <p className="text-sm text-muted-foreground">Set rules and choose “Preview cohort”.</p>}
            {preview.isPending && <Skeleton className="h-64 w-full" />}
            {preview.isError && <ErrorState error={preview.error} />}
            {preview.isSuccess && (
              <>
                <p className="text-lg font-semibold">
                  Cohort size: <Count value={preview.data.count} />
                </p>
                <AttritionChart
                  preview={{
                    ...preview.data,
                    steps: preview.data.steps.map((step) => ({
                      ...step,
                      rule: labelStep(step.rule, previewed?.form.conditions ?? []),
                    })),
                  }}
                />
                <form onSubmit={saveCohort} className="flex flex-wrap items-end gap-2 border-t pt-4">
                  <div className="flex-1 space-y-1.5">
                    <Label htmlFor="cohort-name">Cohort name</Label>
                    <Input id="cohort-name" value={name} maxLength={200} onChange={(e) => setName(e.target.value)} placeholder="e.g. Kidney, dd-cfDNA ≥ 1% in year 1" />
                  </div>
                  <Button type="submit" disabled={!name.trim() || save.isPending}>Save cohort</Button>
                </form>
                {save.isError && <ErrorState error={save.error} />}
                {save.isSuccess && (
                  <p className="text-sm">
                    Saved.{" "}
                    <Link href={`/cohorts/${save.data.cohort_id}`} className="font-medium text-primary hover:underline">
                      Open “{save.data.name}”
                    </Link>
                  </p>
                )}
              </>
            )}
          </CardContent>
        </Card>
      </div>

      <SavedCohorts />
    </div>
  );
}

function SavedCohorts() {
  const cohorts = useCohorts();
  return (
    <section aria-labelledby="saved-heading" className="space-y-3">
      <h2 id="saved-heading" className="text-lg font-semibold">Saved cohorts</h2>
      {cohorts.isPending ? (
        <Skeleton className="h-32 w-full" />
      ) : cohorts.isError ? (
        <ErrorState error={cohorts.error} onRetry={() => cohorts.refetch()} />
      ) : cohorts.data.items.length === 0 ? (
        <EmptyState title="No saved cohorts yet">Preview a cohort above and save it to see it here.</EmptyState>
      ) : (
        <div className="rounded-lg border bg-card">
          <Table>
            <caption className="sr-only">Saved cohorts, newest first</caption>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead className="text-right">Members</TableHead>
                <TableHead>Created</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {cohorts.data.items.map((cohort) => (
                <TableRow key={cohort.cohort_id}>
                  <TableCell>
                    <Link href={`/cohorts/${cohort.cohort_id}`} className="font-medium text-primary hover:underline">{cohort.name}</Link>
                  </TableCell>
                  <TableCell className="text-right tabular-nums"><Count value={cohort.member_count} /></TableCell>
                  <TableCell>{formatDate(cohort.created_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </section>
  );
}
