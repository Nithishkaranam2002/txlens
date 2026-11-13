"use client";

import { useMutation, useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { toast } from "sonner";

import { api, unwrap } from "@/lib/api/client";
import { isApiError } from "@/lib/api/errors";
import { cleanRequest } from "@/lib/survival";
import type { CohortRules, Flag, FlagStatus, Organ, Severity, SurvivalRequest, WorklistPage } from "@/lib/types";

/** `severity`: rows whose highest open severity is one of these. */
export type WorklistFilters = { organ?: Organ; severity?: Severity[]; status?: FlagStatus };

export const keys = {
  worklist: ["worklist"] as const,
  worklistPage: (filters: WorklistFilters, offset: number) => ["worklist", filters, offset] as const,
  patient: (id: string) => ["patient", id] as const,
  timeline: (id: string) => ["patient", id, "timeline"] as const,
  flags: (id: string) => ["patient", id, "flags"] as const,
  risk: (id: string) => ["patient", id, "risk"] as const,
  config: (program: string) => ["config", program] as const,
  cohorts: ["cohorts"] as const,
  cohort: (id: string) => ["cohorts", id] as const,
  members: (id: string, offset: number) => ["cohorts", id, "members", offset] as const,
  concepts: (q: string) => ["concepts", q] as const,
  survivalOptions: ["survival", "options"] as const,
  survival: (request: SurvivalRequest) => ["survival", request] as const,
  conversations: ["copilot", "conversations"] as const,
  conversation: (id: string) => ["copilot", "conversations", id] as const,
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
type AcknowledgeAllInput = { patientId: string; note?: string };

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

/** The worklist page after "acknowledge all": every new flag of that patient is acknowledged. */
export function applyAcknowledgeAll(page: WorklistPage, { patientId, note }: AcknowledgeAllInput): WorklistPage {
  const items = page.items.map((row) =>
    String(row.patient.patient_id) !== patientId
      ? row
      : {
          ...row,
          flags: row.flags.map((flag): Flag =>
            flag.status === "new" ? { ...flag, status: "acknowledged", note: note ?? flag.note } : flag,
          ),
        },
  );
  return { ...page, items };
}

/**
 * Worklist mutations update every cached worklist page at once (optimistic); on failure they
 * roll back and a toast shows the reason and request id.
 */
function optimisticWorklist<T>(
  client: ReturnType<typeof useQueryClient>,
  apply: (page: WorklistPage, input: T) => WorklistPage,
  success: (input: T) => string,
) {
  return {
    onMutate: async (input: T) => {
      await client.cancelQueries({ queryKey: keys.worklist });
      const snapshot = client.getQueriesData<WorklistPage>({ queryKey: keys.worklist });
      client.setQueriesData<WorklistPage>({ queryKey: keys.worklist }, (page) => (page ? apply(page, input) : page));
      return { snapshot };
    },
    onError: (error: Error, _input: T, context: { snapshot: [QueryKey, unknown][] } | undefined) => {
      context?.snapshot.forEach(([key, data]) => client.setQueryData(key, data));
      const requestId = isApiError(error) ? error.requestId : null;
      toast.error(isApiError(error) ? error.message : "The review could not be saved.", {
        description: requestId ? `Request id: ${requestId}` : undefined,
      });
    },
    onSuccess: (_result: unknown, input: T) => {
      toast.success(success(input));
    },
    onSettled: () => {
      client.invalidateQueries({ queryKey: keys.worklist });
      client.invalidateQueries({ queryKey: ["patient"] });
    },
  };
}

/** Acknowledge or resolve one flag. */
export function useReviewFlag() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ flagId, action, note }: ReviewInput) => {
      const options = { params: { path: { flag_id: flagId } }, body: { note: note || null } };
      return action === "acknowledge"
        ? unwrap(await api.POST("/flags/{flag_id}/acknowledge", options))
        : unwrap(await api.POST("/flags/{flag_id}/resolve", options));
    },
    ...optimisticWorklist<ReviewInput>(client, applyReview, ({ action }) =>
      action === "acknowledge" ? "Flag acknowledged" : "Flag resolved",
    ),
  });
}

/** Acknowledge every new flag of one patient. */
export function useAcknowledgeAll() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ patientId, note }: AcknowledgeAllInput) =>
      unwrap(
        await api.POST("/patients/{patient_id}/flags/acknowledge", {
          params: { path: { patient_id: patientId } },
          body: { note: note || null },
        }),
      ),
    ...optimisticWorklist<AcknowledgeAllInput>(client, applyAcknowledgeAll, () => "All new flags acknowledged"),
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

/** A patient's latest rejection-risk score; only for roles that may see individual scores. */
export function useRisk(patientId: string, enabled: boolean) {
  return useQuery({
    queryKey: keys.risk(patientId),
    queryFn: async () =>
      unwrap(await api.GET("/patients/{patient_id}/risk", { params: { path: { patient_id: patientId } } })),
    enabled,
  });
}

/** Score the patient's latest surveillance visit now (clinicians); the card shows the result. */
export function useRescore(patientId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/patients/{patient_id}/risk/score", { params: { path: { patient_id: patientId } } })),
    onSuccess: (risk) => {
      client.setQueryData(keys.risk(patientId), risk);
      toast.success(`Scored with model version ${risk.model_version}`);
    },
    onError: (error) => {
      const requestId = isApiError(error) ? error.requestId : null;
      toast.error(isApiError(error) ? error.message : "The visit could not be scored.", {
        description: requestId ? `Request id: ${requestId}` : undefined,
      });
    },
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

export function useConceptSearch(q: string) {
  const term = q.trim();
  return useQuery({
    queryKey: keys.concepts(term),
    queryFn: async () => unwrap(await api.GET("/concepts/conditions", { params: { query: { q: term, limit: 20 } } })),
    enabled: term.length >= 2,
    staleTime: 5 * 60_000,
  });
}

export function usePreviewCohort() {
  return useMutation({
    mutationFn: async (rules: CohortRules) => unwrap(await api.POST("/cohorts/preview", { body: rules })),
  });
}

export function useSaveCohort() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: { name: string; rules: CohortRules }) => unwrap(await api.POST("/cohorts", { body })),
    onSuccess: (cohort) => {
      client.invalidateQueries({ queryKey: keys.cohorts });
      toast.success(`Cohort “${cohort.name}” saved`);
    },
  });
}

export function useCohorts() {
  return useQuery({
    queryKey: keys.cohorts,
    queryFn: async () => unwrap(await api.GET("/cohorts", { params: { query: { limit: 100 } } })),
  });
}

export function useCohort(id: string) {
  return useQuery({
    queryKey: keys.cohort(id),
    queryFn: async () => unwrap(await api.GET("/cohorts/{cohort_id}", { params: { path: { cohort_id: id } } })),
  });
}

export function useCohortMembers(id: string, offset: number) {
  return useQuery({
    queryKey: keys.members(id, offset),
    queryFn: async () =>
      unwrap(await api.GET("/cohorts/{cohort_id}/members", {
        params: { path: { cohort_id: id }, query: { limit: PAGE_SIZE, offset } },
      })),
    placeholderData: (previous) => previous,
  });
}

export function useSurvivalOptions() {
  return useQuery({
    queryKey: keys.survivalOptions,
    queryFn: async () => unwrap(await api.GET("/survival/options")),
    staleTime: Infinity,
  });
}

/** The analysis for a submitted request (null: nothing submitted yet). */
export function useSurvivalAnalysis(request: SurvivalRequest | null) {
  return useQuery({
    queryKey: keys.survival(request ?? ({} as SurvivalRequest)),
    queryFn: async () => unwrap(await api.POST("/survival/analysis", { body: cleanRequest(request!) })),
    enabled: request !== null,
    staleTime: 5 * 60_000,
    retry: false,
  });
}

export function useConversations() {
  return useQuery({
    queryKey: keys.conversations,
    queryFn: async () => unwrap(await api.GET("/copilot/conversations")),
  });
}

/** A stored conversation; not refetched while it is open (the live stream extends it). */
export function useConversation(id: string | null) {
  return useQuery({
    queryKey: keys.conversation(id ?? ""),
    queryFn: async () =>
      unwrap(await api.GET("/copilot/conversations/{conversation_id}", { params: { path: { conversation_id: id! } } })),
    enabled: id !== null,
    staleTime: Infinity,
  });
}

export function useCreateConversation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (title: string) => unwrap(await api.POST("/copilot/conversations", { body: { title } })),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.conversations }),
  });
}
