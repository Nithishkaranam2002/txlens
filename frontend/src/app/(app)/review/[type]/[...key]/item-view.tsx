"use client";

import { ArrowLeft } from "lucide-react";
import type { Route } from "next";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useState } from "react";

import { DecisionForm } from "@/components/review/decision-form";
import { DocumentEvidence, MappingEvidence, SentenceEvidence } from "@/components/review/evidence";
import { PriorityBadge } from "@/components/review/queue-table";
import { ErrorState, NotFound, PageSkeleton } from "@/components/states";
import { formatDate } from "@/lib/format";
import {
  ITEM_TYPES,
  itemHref,
  keyFromSegments,
  neighbour,
  TYPE_LABEL,
  useReviewItem,
  useReviewQueue,
  type ItemType,
  type ReviewDetail,
} from "@/lib/review";

function Evidence({ detail, active }: { detail: ReviewDetail; active: string | null }) {
  const pane = detail.evidence;
  if (pane.kind === "document") return <DocumentEvidence pane={pane} active={active} />;
  if (pane.kind === "sentence") return <SentenceEvidence pane={pane} />;
  return <MappingEvidence pane={pane} />;
}

function isItemType(value: string): value is ItemType {
  return ITEM_TYPES.some((t) => t.value === value);
}

export function ItemView({ params }: { params: Promise<{ type: string; key: string[] }> }) {
  const { type, key: segments } = use(params);
  if (!isItemType(type)) return <NotFound />;
  return <Item type={type} itemKey={keyFromSegments(segments)} />;
}

/** Evidence on the left, the decision form on the right; j / k move through the queue. */
function Item({ type, itemKey }: { type: ItemType; itemKey: string }) {
  const router = useRouter();
  const detail = useReviewItem(type, itemKey);
  const queue = useReviewQueue({ type, status: "pending" }, 0);
  const [active, setActive] = useState<string | null>(null);
  const items = queue.data?.items ?? [];
  const go = (step: 1 | -1) => {
    const next = neighbour(items, itemKey, step);
    if (next) router.push(itemHref(next.item_type, next.item_key));
  };
  const afterDecision = () => {
    const next = neighbour(items, itemKey, 1);
    router.push(next ? itemHref(next.item_type, next.item_key) : (`/review?type=${type}` as Route));
  };

  if (detail.isPending) return <PageSkeleton />;
  if (detail.isError) return <ErrorState error={detail.error} onRetry={() => detail.refetch()} />;
  const { item } = detail.data;
  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <Link href={`/review?type=${type}` as Route} className="inline-flex items-center gap-1 text-sm text-primary underline-offset-4 hover:underline">
          <ArrowLeft aria-hidden className="size-4" /> Review queue
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">{item.title}</h1>
          <PriorityBadge item={item} />
        </div>
        <p className="text-sm text-muted-foreground">
          {[TYPE_LABEL[type], item.detail, `queued ${formatDate(item.queued_at)}`].filter(Boolean).join(" · ")}
          {item.patient_id && (
            <>
              {" · "}
              <Link href={`/patients/${item.patient_id}` as Route} className="text-primary underline underline-offset-4">
                Patient {item.patient_id}
              </Link>
            </>
          )}
        </p>
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        <section aria-label="Evidence" className="min-w-0">
          <Evidence detail={detail.data} active={active} />
        </section>
        <section aria-label="Decision" className="min-w-0 rounded-lg border bg-card p-4">
          <DecisionForm
            key={`${type}:${itemKey}`}
            detail={detail.data}
            onDone={afterDecision}
            onNext={() => go(1)}
            onPrevious={() => go(-1)}
            onFocusField={setActive}
          />
        </section>
      </div>
    </div>
  );
}
