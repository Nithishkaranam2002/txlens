"use client";

import {
  createColumnHelper,
  createSortedRowModel,
  rowSortingFeature,
  sortFn_alphanumeric,
  sortFn_basic,
  tableFeatures,
  useTable,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";

import { AcknowledgeAllDialog, ReviewDialog } from "@/components/review-dialog";
import { SeverityBadge, StatusBadge } from "@/components/severity-badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDate, ORGAN_LABEL, patientDetail, patientName } from "@/lib/format";
import type { WorklistRow } from "@/lib/types";

type Row = WorklistRow & { rank: number };

const features = tableFeatures({
  rowSortingFeature,
  sortedRowModel: createSortedRowModel(),
  sortFns: { alphanumeric: sortFn_alphanumeric, basic: sortFn_basic },
});
const helper = createColumnHelper<typeof features, Row>();

function PatientCell({ row }: { row: Row }) {
  const patient = row.patient;
  return (
    <div className="min-w-40">
      <Link href={`/patients/${patient.patient_id}`} className="font-medium text-primary underline-offset-4 hover:underline">
        {patientName(patient)}
      </Link>
      <div className="text-xs text-muted-foreground">
        {[patientDetail(patient), ORGAN_LABEL[patient.organ ?? ""]].filter(Boolean).join(" · ")}
      </div>
    </div>
  );
}

/** A flag's own data date, shown only when it differs from the page's "Data as of" date (or
 * when there is none: de-identified views see each patient's shifted dates instead). */
function FlagDate({ asOf, dataAsOf }: { asOf: string; dataAsOf: string | null }) {
  if (asOf === dataAsOf) return null;
  return (
    <span className="ml-1 text-xs text-muted-foreground">
      ({dataAsOf ? "older data, " : ""}as of <time dateTime={asOf}>{formatDate(asOf)}</time>)
    </span>
  );
}

function FlagsCell({ row, canReview, dataAsOf }: { row: Row; canReview: boolean; dataAsOf: string | null }) {
  const subject = patientName(row.patient);
  const fresh = row.flags.filter((flag) => flag.status === "new");
  return (
    <div className="space-y-2">
      {canReview && fresh.length > 1 && (
        <AcknowledgeAllDialog patientId={String(row.patient.patient_id)} flags={fresh} subject={subject} />
      )}
      <ul className="space-y-2">
        {row.flags.map((flag) => (
          <li key={flag.flag_id} className="flex flex-wrap items-start gap-2">
            <SeverityBadge severity={flag.severity} />
            <StatusBadge status={flag.status} />
            <span className="min-w-60 flex-1 text-sm">
              {flag.reason}
              <FlagDate asOf={flag.as_of} dataAsOf={dataAsOf} />
            </span>
            {canReview && (
              <span className="flex gap-1.5">
                {flag.status === "new" && <ReviewDialog flag={flag} action="acknowledge" subject={subject} />}
                <ReviewDialog flag={flag} action="resolve" subject={subject} />
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function columns(canReview: boolean) {
  return helper.columns([
    helper.accessor("rank", { header: "Rank", sortFn: "basic" }),
    helper.accessor((row) => patientName(row.patient), {
      id: "patient",
      header: "Patient",
      sortFn: "alphanumeric",
      cell: ({ row }) => <PatientCell row={row.original} />,
    }),
    helper.accessor("score", {
      header: "Score",
      sortFn: "basic",
      cell: ({ getValue }) => <span className="font-semibold tabular-nums">{getValue()}</span>,
    }),
    helper.display({
      id: "flags",
      header: "Why this patient is flagged",
      cell: ({ row, table }) => (
        <FlagsCell row={row.original} canReview={canReview} dataAsOf={(table.options.meta as Meta).dataAsOf} />
      ),
    }),
  ]);
}

// Stable column definitions (v9 requires stable inputs): one set per permission.
const REVIEW_COLUMNS = columns(true);
const READ_COLUMNS = columns(false);

type Meta = { dataAsOf: string | null };

/** The ranked worklist. Clinicians can acknowledge/resolve; data stewards read only. */
export function WorklistTable({
  rows,
  offset,
  canReview,
  dataAsOf = null,
  caption = "Patients with open surveillance flags, highest priority first",
}: {
  rows: WorklistRow[];
  offset: number;
  canReview: boolean;
  dataAsOf?: string | null;
  caption?: string;
}) {
  const data = useMemo(() => rows.map((row, i) => ({ ...row, rank: offset + i + 1 })), [rows, offset]);
  const meta = useMemo<Meta>(() => ({ dataAsOf }), [dataAsOf]);
  const table = useTable({
    features,
    columns: canReview ? REVIEW_COLUMNS : READ_COLUMNS,
    data,
    meta,
    getRowId: (row) => String(row.patient.patient_id),
  });

  return (
    <div className="rounded-lg border bg-card">
      <Table>
        <caption className="sr-only">{caption}</caption>
        <TableHeader>
          {table.getHeaderGroups().map((group) => (
            <TableRow key={group.id}>
              {group.headers.map((header) => {
                const sorted = header.column.getIsSorted();
                return (
                  <TableHead
                    key={header.id}
                    aria-sort={sorted ? (sorted === "asc" ? "ascending" : "descending") : undefined}
                  >
                    {header.column.getCanSort() ? (
                      <button
                        type="button"
                        onClick={header.column.getToggleSortingHandler()}
                        className="inline-flex items-center gap-1 rounded hover:text-foreground"
                      >
                        <table.FlexRender header={header} />
                        {sorted === "asc" ? (
                          <ArrowUp aria-hidden className="size-3.5" />
                        ) : sorted === "desc" ? (
                          <ArrowDown aria-hidden className="size-3.5" />
                        ) : (
                          <ArrowUpDown aria-hidden className="size-3.5 opacity-50" />
                        )}
                      </button>
                    ) : (
                      <table.FlexRender header={header} />
                    )}
                  </TableHead>
                );
              })}
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
