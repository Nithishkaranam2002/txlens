"use client";

import { createColumnHelper, tableFeatures, useTable } from "@tanstack/react-table";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDate } from "@/lib/format";
import { ACTION_LABEL, itemHref, TYPE_LABEL, type ReviewItem } from "@/lib/review";

const features = tableFeatures({});
const helper = createColumnHelper<typeof features, ReviewItem>();

/** Why the item is where it is in the queue. */
export function PriorityBadge({ item }: { item: ReviewItem }) {
  if (item.rejection_related || item.high_severity) {
    return <Badge variant="destructive">{item.high_severity ? "High severity" : "Rejection"}</Badge>;
  }
  if (item.qc_sample) return <Badge variant="secondary">QC sample</Badge>;
  return <Badge variant="outline">Routine</Badge>;
}

const COLUMNS = helper.columns([
  helper.display({ id: "priority", header: "Priority", cell: ({ row }) => <PriorityBadge item={row.original} /> }),
  helper.display({
    id: "item",
    header: "Item",
    cell: ({ row }) => (
      <div className="min-w-60">
        <Link
          href={itemHref(row.original.item_type, row.original.item_key)}
          className="font-medium text-primary underline-offset-4 hover:underline"
        >
          {row.original.title}
        </Link>
        <div className="text-xs text-muted-foreground">
          {[TYPE_LABEL[row.original.item_type], row.original.detail].filter(Boolean).join(" · ")}
        </div>
      </div>
    ),
  }),
  helper.accessor("patient_id", { header: "Patient", cell: ({ getValue }) => getValue() ?? "—" }),
  helper.accessor("queued_at", { header: "Queued", cell: ({ getValue }) => formatDate(getValue()) }),
  helper.display({
    id: "status",
    header: "Status",
    cell: ({ row }) =>
      row.original.status === "pending" ? (
        "Waiting"
      ) : (
        <span>
          {ACTION_LABEL[row.original.status]}
          <span className="ml-1 text-xs text-muted-foreground">{formatDate(row.original.decided_at)}</span>
        </span>
      ),
  }),
]);

/** The review queue in the API's order: rejection first, then the QC sample, then the rest. */
export function QueueTable({ items }: { items: ReviewItem[] }) {
  const table = useTable({
    features,
    columns: COLUMNS,
    data: items,
    getRowId: (item) => `${item.item_type}:${item.item_key}`,
  });
  return (
    <div className="rounded-lg border bg-card">
      <Table>
        <caption className="sr-only">Items waiting for review, highest priority first</caption>
        <TableHeader>
          {table.getHeaderGroups().map((group) => (
            <TableRow key={group.id}>
              {group.headers.map((header) => (
                <TableHead key={header.id}>
                  <table.FlexRender header={header} />
                </TableHead>
              ))}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.map((row) => (
            <TableRow key={row.id}>
              {row.getAllCells().map((cell) => (
                <TableCell key={cell.id} className="align-top whitespace-normal">
                  <table.FlexRender cell={cell} />
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
