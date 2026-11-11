"use client";

import { useMutation, useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { toast } from "sonner";

import { api, unwrap } from "@/lib/api/client";
import { isApiError } from "@/lib/api/errors";
import type { Flag, FlagStatus, Organ, Severity, WorklistPage } from "@/lib/types";

export type WorklistFilters = { organ?: Organ; severity?: Severity; status?: FlagStatus };

export const keys = {
  worklist: ["worklist"] as const,
  worklistPage: (filters: WorklistFilters, offset: number) => ["worklist", filters, offset] as const,
  patient: (id: string) => ["patient", id] as const,
  timeline: (id: string) => ["patient", id, "timeline"] as const,
  flags: (id: string) => ["patient", id, "flags"] as const,
  config: (program: string) => ["config", program] as const,
  cohorts: ["cohorts"] as const,
  cohort: (id: string) => ["cohorts", id] as const,
  members: (id: string, offset: number) => ["cohorts", id, "members", offset] as const,
  concepts: (q: string) => ["concepts", q] as const,
};

export const PAGE_SIZE = 50;

export function useWorklist(filters: WorklistFilters, offset: number) {
  return useQuery({
    queryKey: keys.worklistPage(filters, offset),
    queryFn: async () =>
      unwrap(await api.GET("/worklist", { params: { query: { ...filters, limit: PAGE_SIZE, offset } } })),
    placeholderData: (previous) => previous,
  });
}

export type ReviewAction = "acknowledge" | "resolve";
type ReviewInput = { flagId: string; action: ReviewAction; note?: string };

/** The worklist page after a review: acknowledged flags change status, resolved ones leave. */
export function applyReview(page: WorklistPage, { flagId, action, note }: ReviewInput): WorklistPage {
  const update = (flag: Flag): Flag | null => {
    if (flag.flag_id !== flagId) return flag;
    if (action === "resolve") return null;
    return { ...flag, status: "acknowledged", note: note ?? flag.note };
  };
  const items = page.items
    .map((row) => ({ ...row, flags: row.flags.map(update).filter((f): f is Flag => f !== null) }))
    .filter((row) => row.flags.length > 0);
  return { ...page, items, total: page.total - (page.items.length - items.length) };
}

/**
 * Acknowledge or resolve a flag. The worklist updates at once (optimistic); on failure it
 * rolls back and a toast shows the reason and request id.
 */
export function useReviewFlag() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ flagId, action, note }: ReviewInput) => {
      const options = { params: { path: { flag_id: flagId } }, body: { note: note || null } };
      return action === "acknowledge"
        ? unwrap(await api.POST("/flags/{flag_id}/acknowledge", options))
        : unwrap(await api.POST("/flags/{flag_id}/resolve", options));
    },
    onMutate: async (input) => {
      await client.cancelQueries({ queryKey: keys.worklist });
      const snapshot = client.getQueriesData<WorklistPage>({ queryKey: keys.worklist });
      client.setQueriesData<WorklistPage>({ queryKey: keys.worklist }, (page) =>
        page ? applyReview(page, input) : page,
      );
      return { snapshot };
    },
    onError: (error, _input, context) => {
      context?.snapshot.forEach(([key, data]: [QueryKey, unknown]) => client.setQueryData(key, data));
      const requestId = isApiError(error) ? error.requestId : null;
      toast.error(isApiError(error) ? error.message : "The review could not be saved.", {
        description: requestId ? `Request id: ${requestId}` : undefined,
      });
    },
    onSuccess: (_flag, { action }) => {
      toast.success(action === "acknowledge" ? "Flag acknowledged" : "Flag resolved");
    },
    onSettled: () => {
      client.invalidateQueries({ queryKey: keys.worklist });
      client.invalidateQueries({ queryKey: ["patient"] });
    },
  });
}

export function useTimeline(patientId: string) {
  return useQuery({
    queryKey: keys.timeline(patientId),
    queryFn: async () =>
      unwrap(await api.GET("/patients/{patient_id}/timeline", { params: { path: { patient_id: patientId } } })),
  });
}

/** A patient's flags with history; only for roles that may see flags. */
export function usePatientFlags(patientId: string, enabled: boolean) {
  return useQuery({
    queryKey: keys.flags(patientId),
    queryFn: async () =>
      unwrap(await api.GET("/patients/{patient_id}/flags", { params: { path: { patient_id: patientId } } })),
    enabled,
  });
}

/** The surveillance config (thresholds, target ranges) for an organ program. */
export function useSurveillanceConfig(program: string | null | undefined) {
  return useQuery({
    queryKey: keys.config(program ?? ""),
    queryFn: async () =>
      unwrap(await api.GET("/surveillance/configs/{program}", { params: { path: { program: program! } } })),
    enabled: Boolean(program),
    staleTime: Infinity,
  });
}
