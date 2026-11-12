"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Route } from "next";
import { toast } from "sonner";

import { api, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { isApiError } from "@/lib/api/errors";

type Schemas = components["schemas"];
export type ItemType = Schemas["ReviewItem"]["item_type"];
export type ReviewItem = Schemas["ReviewItem"];
export type QueuePage = Schemas["QueuePage"];
export type ReviewDetail = Schemas["ReviewDetail"];
export type ReviewField = Schemas["ReviewField"];
export type DecisionAction = Schemas["DecisionRequest"]["action"];
export type ReasonCode = NonNullable<Schemas["DecisionRequest"]["reason_code"]>;
export type DocumentPane = Schemas["DocumentPane"];
export type SentencePane = Schemas["SentencePane"];
export type MappingPane = Schemas["MappingPane"];
export type ReviewMetrics = Schemas["ReviewMetrics"];
export type QueueStatus = "pending" | "decided";
export type QueueFilters = { type?: ItemType; status: QueueStatus };

export const ITEM_TYPES: readonly { value: ItemType; label: string }[] = [
  { value: "biopsy_document", label: "Biopsy report" },
  { value: "note_mention", label: "Note mention" },
  { value: "condition_mapping", label: "Condition mapping" },
  { value: "curated_mapping", label: "AI-curated mapping" },
];
export const TYPE_LABEL = Object.fromEntries(ITEM_TYPES.map((t) => [t.value, t.label])) as Record<ItemType, string>;

export const REASON_LABEL: Record<ReasonCode, string> = {
  ocr_error: "OCR misread the text",
  wrong_value: "Wrong value",
  wrong_concept: "Wrong concept",
  wrong_negation: "Wrong negation",
  wrong_temporality: "Wrong time (current / past / planned)",
  wrong_experiencer: "Wrong person (patient / family)",
  not_a_finding: "Not a real finding",
  insufficient_evidence: "Evidence does not support it",
  other: "Other (explain in the note)",
};

export const ACTION_LABEL: Record<DecisionAction, string> = {
  approve: "Approve",
  edit: "Save edit",
  reject: "Reject",
  revert: "Revert",
};

export const PAGE_SIZE = 50;

export const reviewKeys = {
  all: ["review"] as const,
  queue: (filters: QueueFilters, offset: number) => ["review", "queue", filters, offset] as const,
  item: (type: ItemType, key: string) => ["review", "item", type, key] as const,
  metrics: ["review", "metrics"] as const,
};

/** The URL of an item's page. Keys may hold "/" (curated "mixed tcmr/abmr"): one segment each. */
export function itemHref(type: ItemType, key: string): Route {
  return `/review/${type}/${key.split("/").map(encodeURIComponent).join("/")}` as Route;
}

/** The key back from the catch-all route segments. */
export function keyFromSegments(segments: string[]): string {
  return segments.map(decodeURIComponent).join("/");
}

/** Why a decision cannot be sent yet, or null. Mirrors the API's rules. */
export function decisionProblem(action: DecisionAction, reason: ReasonCode | null, note: string): string | null {
  if (action !== "approve" && reason === null) return "Choose a reason for this decision.";
  if (reason === "other" && note.trim() === "") return "Explain the reason in the note.";
  return null;
}

/** Only the fields whose value changed. */
export function changedFields(fields: ReviewField[], values: Record<string, string>): Record<string, string> {
  return Object.fromEntries(
    Object.entries(values).filter(([name, value]) => fields.find((f) => f.name === name)?.value !== value),
  );
}

/** A box in PDF points scaled to the displayed image (CSS pixels). */
export function scaleBox(
  bbox: number[],
  page: number[],
  shown: { width: number; height: number },
): { left: number; top: number; width: number; height: number } {
  const [x0, y0, x1, y1] = bbox;
  const sx = shown.width / page[0];
  const sy = shown.height / page[1];
  return { left: x0 * sx, top: y0 * sy, width: (x1 - x0) * sx, height: (y1 - y0) * sy };
}

/** The item before or after `key` in the queue (for j / k), or null at either end. */
export function neighbour(items: readonly ReviewItem[], key: string, step: 1 | -1): ReviewItem | null {
  const index = items.findIndex((item) => item.item_key === key);
  return index === -1 ? null : (items[index + step] ?? null);
}

export function useReviewQueue(filters: QueueFilters, offset: number) {
  return useQuery({
    queryKey: reviewKeys.queue(filters, offset),
    queryFn: async () =>
      unwrap(
        await api.GET("/review/items", {
          params: { query: { type: filters.type, status: filters.status, limit: PAGE_SIZE, offset } },
        }),
      ),
    placeholderData: (previous) => previous,
  });
}

export function useReviewItem(type: ItemType, key: string) {
  return useQuery({
    queryKey: reviewKeys.item(type, key),
    queryFn: async () =>
      unwrap(await api.GET("/review/items/{item_type}/{item_key}", { params: { path: { item_type: type, item_key: key } } })),
  });
}

export function useReviewMetrics() {
  return useQuery({
    queryKey: reviewKeys.metrics,
    queryFn: async () => unwrap(await api.GET("/review/metrics")),
  });
}

export type DecisionInput = {
  type: ItemType;
  key: string;
  action: DecisionAction;
  reason: ReasonCode | null;
  note: string;
  changes: Record<string, string>;
};

/** Send a decision; on success the queue and the item reload, on failure a toast explains. */
export function useDecide() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ type, key, action, reason, note, changes }: DecisionInput) =>
      unwrap(
        await api.POST("/review/items/{item_type}/{item_key}", {
          params: { path: { item_type: type, item_key: key } },
          body: { action, reason_code: reason, note: note.trim() || null, changes: action === "edit" ? changes : {} },
        }),
      ),
    onSuccess: (result, { action }) => {
      toast.success(
        result.status === "submitted"
          ? `${ACTION_LABEL[action]} sent to the document workflow`
          : `${ACTION_LABEL[action]} saved`,
      );
    },
    onError: (error) => {
      const requestId = isApiError(error) ? error.requestId : null;
      toast.error(isApiError(error) ? error.message : "The decision could not be saved.", {
        description: requestId ? `Request id: ${requestId}` : undefined,
      });
    },
    onSettled: () => client.invalidateQueries({ queryKey: reviewKeys.all }),
  });
}
