"use client";

import type { Route } from "next";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { FilterSelect } from "@/components/filter-select";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/states";
import { Button } from "@/components/ui/button";
import { WorklistTable } from "@/components/worklist-table";
import { useRole } from "@/lib/auth";
import { PAGE_SIZE, useWorklist, type WorklistFilters } from "@/lib/queries";
import { can, seesIdentifiers } from "@/lib/roles";
import type { FlagStatus, Organ, Severity } from "@/lib/types";

const ORGANS = [{ value: "kidney", label: "Kidney" }, { value: "heart", label: "Heart" }] as const;
const SEVERITIES = [
  { value: "high", label: "High" },
  { value: "medium", label: "Medium" },
  { value: "low", label: "Low" },
] as const;
const STATUSES = [{ value: "new", label: "New" }, { value: "acknowledged", label: "Acknowledged" }] as const;

function pick<T extends string>(value: string | null, allowed: readonly { value: T }[]): T | undefined {
  return allowed.find((option) => option.value === value)?.value;
}

/** Filters and paging live in the URL (they hold no patient data), so views can be shared. */
export function WorklistView() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const role = useRole();
  const filters: WorklistFilters = {
    organ: pick<Organ>(params.get("organ"), ORGANS),
    severity: pick<Severity>(params.get("severity"), SEVERITIES),
    status: pick<FlagStatus>(params.get("status"), STATUSES),
  };
  const offset = Math.max(0, Number(params.get("offset")) || 0);
  const worklist = useWorklist(filters, offset);

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
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Surveillance worklist</h1>
          <p className="text-sm text-muted-foreground">
            Patients whose results need review, highest priority first.
            {!seesIdentifiers(role) && " De-identified view: pseudonyms, age bands and shifted dates."}
          </p>
        </div>
        <div className="flex flex-wrap gap-3" role="group" aria-label="Filters">
          <FilterSelect id="filter-organ" label="Organ" value={filters.organ} options={ORGANS} onChange={(v) => update({ organ: v })} />
          <FilterSelect id="filter-severity" label="Severity" value={filters.severity} options={SEVERITIES} onChange={(v) => update({ severity: v })} />
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
          <WorklistTable rows={worklist.data.items} offset={offset} canReview={can(role, "review")} />
          {total > PAGE_SIZE && (
            <nav aria-label="Pages" className="flex items-center justify-end gap-2">
              <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => update({ offset: String(Math.max(0, offset - PAGE_SIZE)) })}>
                Previous
              </Button>
              <span className="text-sm text-muted-foreground">
                {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
              </span>
              <Button variant="outline" size="sm" disabled={offset + PAGE_SIZE >= total} onClick={() => update({ offset: String(offset + PAGE_SIZE) })}>
                Next
              </Button>
            </nav>
          )}
        </>
      )}
    </div>
  );
}
