"use client";

import { ArrowLeft, LineChart } from "lucide-react";
import Link from "next/link";
import { use, useState } from "react";

import { SuppressedCount } from "@/components/charts/attrition-chart";
import { MembersTable } from "@/components/members-table";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { describeRules } from "@/lib/cohort-form";
import { formatCount, formatDate } from "@/lib/format";
import { PAGE_SIZE, useCohort, useCohortMembers } from "@/lib/queries";

export function CohortView({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [offset, setOffset] = useState(0);
  const cohort = useCohort(id);
  const members = useCohortMembers(id, offset);

  if (cohort.isPending) return <PageSkeleton />;
  if (cohort.isError) return <ErrorState error={cohort.error} onRetry={() => cohort.refetch()} />;

  const total = members.data?.total;
  const numericTotal = typeof total === "number" ? total : 0;
  return (
    <div className="space-y-6">
      <Link href="/cohorts" className="inline-flex items-center gap-1 text-sm text-primary hover:underline">
        <ArrowLeft aria-hidden className="size-4" /> All cohorts
      </Link>
      <Card>
        <CardContent className="space-y-3">
          <h1 className="text-2xl font-semibold tracking-tight">{cohort.data.name}</h1>
          <p className="text-sm text-muted-foreground">
            Saved {formatDate(cohort.data.created_at)} · members frozen at save time ·{" "}
            {typeof cohort.data.member_count === "string" ? <SuppressedCount value={cohort.data.member_count} /> : formatCount(cohort.data.member_count)}{" "}
            members
          </p>
          <ul className="list-disc space-y-0.5 pl-5 text-sm">
            {describeRules(cohort.data.rules).map((line) => <li key={line}>{line}</li>)}
          </ul>
          <Button asChild variant="outline" size="sm">
            <Link href={{ pathname: "/survival", query: { cohort: id } }}>
              <LineChart aria-hidden className="size-4" /> Survival analysis of this cohort
            </Link>
          </Button>
        </CardContent>
      </Card>

      <section aria-labelledby="members-heading" className="space-y-3">
        <h2 id="members-heading" className="text-lg font-semibold">Members</h2>
        {members.isPending ? (
          <Skeleton className="h-48 w-full" />
        ) : members.isError ? (
          <ErrorState error={members.error} onRetry={() => members.refetch()} />
        ) : members.data.items.length === 0 ? (
          <EmptyState title="No members">No patients matched these rules when the cohort was saved.</EmptyState>
        ) : (
          <>
            <MembersTable patients={members.data.items} />
            {numericTotal > PAGE_SIZE && (
              <nav aria-label="Pages" className="flex items-center justify-end gap-2">
                <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</Button>
                <span className="text-sm text-muted-foreground">{offset + 1}–{Math.min(offset + PAGE_SIZE, numericTotal)} of {numericTotal}</span>
                <Button variant="outline" size="sm" disabled={offset + PAGE_SIZE >= numericTotal} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</Button>
              </nav>
            )}
          </>
        )}
      </section>
    </div>
  );
}
