"use client";

import type { Route } from "next";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { FilterSelect } from "@/components/filter-select";
import { QueueTable } from "@/components/review/queue-table";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/states";
import { Button } from "@/components/ui/button";
import { useRole } from "@/lib/auth";
import { ITEM_TYPES, PAGE_SIZE, TYPE_LABEL, useReviewQueue, type ItemType, type QueueStatus } from "@/lib/review";
import { seesIdentifiers } from "@/lib/roles";

const STATUSES = [
  { value: "pending", label: "Waiting" },
  { value: "decided", label: "Decided" },
] as const;

function pick<T extends string>(value: string | null, allowed: readonly { value: T }[]): T | undefined {
  return allowed.find((option) => option.value === value)?.value;
}

/** Filters and paging live in the URL (no patient data in them), so a view can be shared. */
export function QueueView() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const role = useRole();
  const type = pick<ItemType>(params.get("type"), ITEM_TYPES);
  const status: QueueStatus = pick<QueueStatus>(params.get("status"), STATUSES) ?? "pending";
  const offset = Math.max(0, Number(params.get("offset")) || 0);
  const queue = useReviewQueue({ type, status }, offset);

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

  const total = queue.data?.total ?? 0;
  const counts = queue.data?.pending_by_type;
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Review queue</h1>
          <p className="text-sm text-muted-foreground">
            Extractions and mappings waiting for a person: rejection and high severity first, then the QC sample.
            {!seesIdentifiers(role) && " De-identified view: pseudonyms, redacted evidence and shifted dates."}{" "}
            <Link href="/review/metrics" className="text-primary underline underline-offset-4">
              Review metrics
            </Link>
          </p>
        </div>
        <div className="flex flex-wrap gap-3" role="group" aria-label="Filters">
          <FilterSelect id="filter-type" label="Type" value={type} options={ITEM_TYPES} width="w-48" onChange={(v) => update({ type: v })} />
          <FilterSelect
            id="filter-status"
            label="Status"
            value={status}
            options={STATUSES}
            allowAll={false}
            onChange={(v) => update({ status: v === "pending" ? undefined : v })}
          />
        </div>
      </div>

      {counts && (
        <ul aria-label="Waiting per type" className="flex flex-wrap gap-2">
          {ITEM_TYPES.map(({ value }) => (
            <li key={value}>
              <Button
                size="sm"
                variant={type === value ? "default" : "outline"}
                aria-pressed={type === value}
                onClick={() => update({ type: type === value ? undefined : value })}
              >
                {TYPE_LABEL[value]} <span className="tabular-nums">{counts[value] ?? 0}</span>
              </Button>
            </li>
          ))}
        </ul>
      )}

      {queue.isPending ? (
        <PageSkeleton />
      ) : queue.isError ? (
        <ErrorState error={queue.error} onRetry={() => queue.refetch()} />
      ) : queue.data.items.length === 0 ? (
        <EmptyState title={status === "pending" ? "Nothing waiting for review" : "No decisions yet"}>
          {status === "pending" ? "Items appear here when an extraction or mapping needs a person." : "Decided items appear here."}
        </EmptyState>
      ) : (
        <>
          <p className="text-sm text-muted-foreground" aria-live="polite">
            {total} item{total === 1 ? "" : "s"} {status === "pending" ? "waiting" : "decided"}
          </p>
          <QueueTable items={queue.data.items} />
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
