"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { use, useMemo } from "react";

import { DdcfdnaChart, DsaChart, KidneyFunctionChart, TacrolimusChart } from "@/components/charts/lab-charts";
import { FlagList } from "@/components/flag-list";
import { PatientHeader } from "@/components/patient-header";
import { PatientTimeline } from "@/components/patient-timeline";
import { ErrorState, PageSkeleton } from "@/components/states";
import { Skeleton } from "@/components/ui/skeleton";
import { useRole } from "@/lib/auth";
import { seriesByLab } from "@/lib/chart-data";
import { patientName } from "@/lib/format";
import { usePatientFlags, useSurveillanceConfig, useTimeline } from "@/lib/queries";
import { can, homeFor } from "@/lib/roles";

export function PatientView({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const role = useRole();
  const showFlags = can(role, "flags");
  const timeline = useTimeline(id);
  const flags = usePatientFlags(id, showFlags);
  const config = useSurveillanceConfig(timeline.data?.patient.organ);
  const series = useMemo(() => seriesByLab(timeline.data?.labs ?? []), [timeline.data]);
  const flagList = useMemo(() => flags.data?.flags ?? [], [flags.data]);

  if (timeline.isPending) return <PageSkeleton />;
  if (timeline.isError) return <ErrorState error={timeline.error} onRetry={() => timeline.refetch()} />;

  const { patient } = timeline.data;
  const chartProps = { series, config: config.data ?? null, flags: flagList };
  return (
    <div className="space-y-6">
      <Link href={homeFor(role)} className="inline-flex items-center gap-1 text-sm text-primary hover:underline">
        <ArrowLeft aria-hidden className="size-4" /> Back
      </Link>
      <PatientHeader patient={patient} />

      <section aria-labelledby="results-heading" className="space-y-3">
        <h2 id="results-heading" className="text-lg font-semibold">Results</h2>
        <div className="grid gap-4 xl:grid-cols-2">
          <DdcfdnaChart {...chartProps} />
          <KidneyFunctionChart {...chartProps} />
          <TacrolimusChart {...chartProps} transplantDate={patient.transplant_date} />
          <DsaChart {...chartProps} />
        </div>
      </section>

      {showFlags &&
        (flags.isPending ? (
          <Skeleton className="h-40 w-full" />
        ) : flags.isError ? (
          <ErrorState error={flags.error} onRetry={() => flags.refetch()} />
        ) : (
          <FlagList flags={flags.data.flags} canReview={can(role, "review")} subject={patientName(patient)} />
        ))}

      <PatientTimeline timeline={timeline.data} />
    </div>
  );
}
