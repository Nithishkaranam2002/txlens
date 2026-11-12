import { API_BASE, sessionFetch } from "@/lib/api/client";
import { toApiError } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import type { Preview, SurvivalReport } from "@/lib/types";

export type Conversation = components["schemas"]["Conversation"];
export type ConversationSummary = components["schemas"]["ConversationSummary"];
type StoredMessage = components["schemas"]["Message"];

/** What a tool shows inline: an attrition funnel, survival curves, or an aggregate table. */
export type ToolDisplay =
  | { kind: "attrition"; preview: Preview }
  | { kind: "survival"; report: SurvivalReport }
  | { kind: "table"; columns: string[]; rows: (string | number | boolean | null)[][]; notices: string[] };

export type ToolEvent = {
  type: "tool";
  step: number;
  tool: string;
  label: string;
  args: Record<string, unknown>;
  ok: boolean;
  error: string | null;
  sql: string | null;
  display: ToolDisplay | null;
  notices: string[];
  latency_ms: number;
};

export type Citation = { pmid: number; title: string; journal: string; year: number | null; snippet: string; url: string };

export type DoneEvent = {
  type: "done";
  message_id: string;
  outcome: "answered" | "refused" | "error";
  answer: string;
  cost_usd: number;
  latency_ms: number;
};

/** The server-sent events of one answer (see backend api/copilot.py). */
export type CopilotEvent =
  | { type: "status"; stage: "screening" | "planning" | "answering" }
  | ToolEvent
  | { type: "token"; text: string }
  | { type: "citations"; citations: Citation[] }
  | { type: "refusal"; reason: string; text: string }
  | { type: "error"; message: string }
  | DoneEvent;

/** One question and everything shown for its answer. */
export type Turn = {
  question: string;
  stage: string | null;
  tools: ToolEvent[];
  text: string;
  citations: Citation[];
  refusal: string | null;
  error: string | null;
  done: boolean;
  costUsd: number | null;
  latencyMs: number | null;
};

export function newTurn(question: string): Turn {
  return { question, stage: "screening", tools: [], text: "", citations: [], refusal: null, error: null, done: false, costUsd: null, latencyMs: null };
}

/** The turn after one more event (pure, so the stream is easy to test). */
export function applyEvent(turn: Turn, event: CopilotEvent): Turn {
  switch (event.type) {
    case "status":
      return { ...turn, stage: event.stage };
    case "tool":
      return { ...turn, tools: [...turn.tools, event] };
    case "token":
      return { ...turn, text: turn.text + event.text };
    case "citations":
      return { ...turn, citations: event.citations };
    case "refusal":
      return { ...turn, refusal: event.text, text: event.text };
    case "error":
      return { ...turn, error: event.message };
    case "done":
      return { ...turn, done: true, stage: null, costUsd: event.cost_usd, latencyMs: event.latency_ms };
  }
}

/** Complete events in a text/event-stream buffer, and the unfinished rest. */
export function parseEvents(buffer: string): { events: CopilotEvent[]; rest: string } {
  const blocks = buffer.split("\n\n");
  const rest = blocks.pop() ?? "";
  const events = blocks
    .map((block) => block.split("\n").find((line) => line.startsWith("data: ")))
    .filter((line): line is string => line !== undefined)
    .map((line) => JSON.parse(line.slice("data: ".length)) as CopilotEvent);
  return { events, rest };
}

/** Ask a question; calls `onEvent` for each event as it arrives. Throws ApiError on 4xx/5xx. */
export async function askCopilot(
  conversationId: string,
  question: string,
  onEvent: (event: CopilotEvent) => void,
  fetchImpl = sessionFetch(),
): Promise<void> {
  const response = await fetchImpl(
    new Request(`${location.origin}${API_BASE}/copilot/conversations/${conversationId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ question }),
    }),
  );
  if (!response.ok || !response.body) {
    throw toApiError(response, await response.json().catch(() => undefined));
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    const parsed = parseEvents(buffer + value);
    buffer = parsed.rest;
    parsed.events.forEach(onEvent);
  }
}

/** Stored messages as turns (a question and the answer that followed it). */
export function turnsOf(conversation: Conversation): Turn[] {
  const turns: Turn[] = [];
  for (const message of conversation.messages as StoredMessage[]) {
    if (message.role === "user") {
      turns.push({ ...newTurn(message.content), stage: null });
      continue;
    }
    const turn = turns.at(-1);
    if (!turn) continue;
    Object.assign(turn, {
      text: message.content,
      tools: message.trace as ToolEvent[],
      citations: message.citations as Citation[],
      refusal: message.outcome === "refused" ? message.content : null,
      error: message.outcome === "error" ? message.content : null,
      done: true,
      costUsd: message.cost_usd,
      latencyMs: message.latency_ms,
    });
  }
  return turns;
}

/** Answer text split into plain text, bold text and PMID citations (rendered as links). */
export type Piece = { kind: "text" | "bold"; text: string } | { kind: "pmid"; pmid: number };

export function pieces(line: string): Piece[] {
  const out: Piece[] = [];
  const pattern = /\[PMID:\s*(\d+)\]|\*\*([^*]+)\*\*/g;
  let last = 0;
  for (const match of line.matchAll(pattern)) {
    if (match.index > last) out.push({ kind: "text", text: line.slice(last, match.index) });
    out.push(match[1] ? { kind: "pmid", pmid: Number(match[1]) } : { kind: "bold", text: match[2] });
    last = match.index + match[0].length;
  }
  if (last < line.length) out.push({ kind: "text", text: line.slice(last) });
  return out;
}

export const STAGE_LABEL: Record<string, string> = {
  screening: "Checking the question…",
  planning: "Choosing a tool…",
  answering: "Writing the answer…",
};
