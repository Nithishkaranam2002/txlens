"use client";

import type { Route } from "next";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { FilterSelect } from "@/components/filter-select";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/states";
import { Button } from "@/components/ui/button";
import { WorklistTable } from "@/components/worklist-table";
import { useRole } from "@/lib/auth";
import { formatDate } from "@/lib/format";
import { PAGE_SIZE, useWorklist, type WorklistFilters } from "@/lib/queries";
import { can, seesIdentifiers } from "@/lib/roles";
import type { FlagStatus, Organ, Severity } from "@/lib/types";

const ORGANS = [{ value: "kidney", label: "Kidney" }, { value: "heart", label: "Heart" }] as const;
// Rows are filtered by the patient's highest open severity. The default leaves low-only
// patients to the collapsed section below the list.
const PRIORITIES = [
  { value: "high-medium", label: "High + Medium" },
  { value: "high", label: "High" },
  { value: "medium", label: "Medium" },
  { value: "low", label: "Low" },
  { value: "all", label: "All" },
] as const;
type Priority = (typeof PRIORITIES)[number]["value"];
const DEFAULT_PRIORITY: Priority = "high-medium";
const SEVERITIES: Record<Priority, Severity[] | undefined> = {
  "high-medium": ["high", "medium"],
  high: ["high"],
  medium: ["medium"],
  low: ["low"],
  all: undefined,
};
const STATUSES = [{ value: "new", label: "New" }, { value: "acknowledged", label: "Acknowledged" }] as const;

function pick<T extends string>(value: string | null, allowed: readonly { value: T }[]): T | undefined {
  return allowed.find((option) => option.value === value)?.value;
}

function Pager({ offset, total, onChange }: { offset: number; total: number; onChange: (offset: number) => void }) {
  if (total <= PAGE_SIZE) return null;
  return (
    <nav aria-label="Pages" className="flex items-center justify-end gap-2">
      <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - PAGE_SIZE))}>
        Previous
      </Button>
      <span className="text-sm text-muted-foreground">
        {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
      </span>
      <Button variant="outline" size="sm" disabled={offset + PAGE_SIZE >= total} onClick={() => onChange(offset + PAGE_SIZE)}>
        Next
      </Button>
    </nav>
  );
}

/** Patients whose open flags are all low severity, collapsed under the main list. */
function LowPriority({ filters, canReview }: { filters: WorklistFilters; canReview: boolean }) {
  const [offset, setOffset] = useState(0);
  const low = useWorklist({ ...filters, severity: ["low"] }, offset);
  const total = low.data?.total ?? 0;
  if (!low.data || total === 0) return null;
  return (
    <details className="group rounded-lg border bg-card">
      <summary className="cursor-pointer rounded-lg px-4 py-3 text-sm font-medium focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
        Low priority ({total} patient{total === 1 ? "" : "s"})
      </summary>
      <div className="space-y-3 border-t p-3">
        <WorklistTable
          rows={low.data.items}
          offset={offset}
          canReview={canReview}
          dataAsOf={low.data.data_as_of}
          caption="Patients whose open flags are all low severity"
        />
        <Pager offset={offset} total={total} onChange={setOffset} />
      </div>
    </details>
  );
}

/** Filters and paging live in the URL (they hold no patient data), so views can be shared. */
export function WorklistView() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const role = useRole();
  const priority = pick<Priority>(params.get("severity"), PRIORITIES) ?? DEFAULT_PRIORITY;
  const filters: WorklistFilters = {
    organ: pick<Organ>(params.get("organ"), ORGANS),
    severity: SEVERITIES[priority],
    status: pick<FlagStatus>(params.get("status"), STATUSES),
  };
  const offset = Math.max(0, Number(params.get("offset")) || 0);
  const worklist = useWorklist(filters, offset);
  const canReview = can(role, "review");

  const update = (changes: Record<string, string | undefined>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value === undefined) next.delete(key);
      else next.set(key, value);
    }
    if (!("offset" in changes)) next.delete("offset");
    const query = next.toString();
    router.replace(`${pathname}${query ? `?${query}` : ""}` as Route, { scroll: false });
  };

  const total = worklist.data?.total ?? 0;
  const dataAsOf = worklist.data?.data_as_of ?? null;
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Surveillance worklist</h1>
          <p className="text-sm text-muted-foreground">
            Patients whose results need review, highest priority first.
            {!seesIdentifiers(role) && " De-identified view: pseudonyms, age bands and shifted dates."}
          </p>
          {dataAsOf && (
            <p className="mt-1 text-sm">
              Data as of <time dateTime={dataAsOf} className="font-medium">{formatDate(dataAsOf)}</time>
            </p>
          )}
        </div>
        <div className="flex flex-wrap gap-3" role="group" aria-label="Filters">
          <FilterSelect id="filter-organ" label="Organ" value={filters.organ} options={ORGANS} onChange={(v) => update({ organ: v })} />
          <FilterSelect
            id="filter-severity"
            label="Highest severity"
            value={priority}
            options={PRIORITIES}
            allowAll={false}
            onChange={(v) => update({ severity: v === DEFAULT_PRIORITY ? undefined : v })}
          />
          <FilterSelect id="filter-status" label="Status" value={filters.status} options={STATUSES} onChange={(v) => update({ status: v })} />
        </div>
      </div>

      {worklist.isPending ? (
        <PageSkeleton />
      ) : worklist.isError ? (
        <ErrorState error={worklist.error} onRetry={() => worklist.refetch()} />
      ) : worklist.data.items.length === 0 ? (
        <EmptyState title="No patients to review">
          No open flags match these filters. Flags appear here after the nightly surveillance run.
        </EmptyState>
      ) : (
        <>
          <p className="text-sm text-muted-foreground" aria-live="polite">
            {total} patient{total === 1 ? "" : "s"} with open flags
          </p>
          <WorklistTable rows={worklist.data.items} offset={offset} canReview={canReview} dataAsOf={dataAsOf} />
          <Pager offset={offset} total={total} onChange={(next) => update({ offset: String(next) })} />
        </>
      )}
      {priority === DEFAULT_PRIORITY && <LowPriority filters={filters} canReview={canReview} />}
    </div>
  );
}
