import { describe, expect, it } from "vitest";

import { applyEvent, newTurn, parseEvents, pieces, turnsOf, type Conversation, type CopilotEvent } from "./copilot";

const sse = (event: CopilotEvent) => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`;

describe("parseEvents", () => {
  it("returns complete events and keeps a partial one for later", () => {
    const whole = sse({ type: "token", text: "Hello " }) + sse({ type: "status", stage: "answering" });
    const partial = 'event: token\ndata: {"type":"tok';
    const { events, rest } = parseEvents(whole + partial);
    expect(events).toEqual([{ type: "token", text: "Hello " }, { type: "status", stage: "answering" }]);
    expect(rest).toBe(partial);
    expect(parseEvents(rest + 'en","text":"x"}\n\n').events).toEqual([{ type: "token", text: "x" }]);
  });
});

describe("applyEvent", () => {
  it("builds a turn from the stream", () => {
    const events: CopilotEvent[] = [
      { type: "status", stage: "planning" },
      { type: "tool", step: 1, tool: "sql_query", label: "ran SQL", args: {}, ok: true, error: null, sql: "SELECT 1", display: null, notices: [], latency_ms: 5 },
      { type: "token", text: "There are " },
      { type: "token", text: "427." },
      { type: "citations", citations: [] },
      { type: "done", message_id: "m", outcome: "answered", answer: "There are 427.", cost_usd: 0.001, latency_ms: 900 },
    ];
    const turn = events.reduce(applyEvent, newTurn("How many?"));
    expect(turn.text).toBe("There are 427.");
    expect(turn.tools.map((t) => t.label)).toEqual(["ran SQL"]);
    expect([turn.done, turn.stage, turn.costUsd]).toEqual([true, null, 0.001]);
  });

  it("shows a refusal as the answer", () => {
    const turn = applyEvent(newTurn("MRNs?"), { type: "refusal", reason: "identifiers", text: "I can't share identifiers." });
    expect([turn.refusal, turn.text]).toEqual(["I can't share identifiers.", "I can't share identifiers."]);
  });
});

describe("pieces", () => {
  it("splits text, bold and PMID citations", () => {
    expect(pieces("A **1%** cut-off [PMID:123] works.")).toEqual([
      { kind: "text", text: "A " },
      { kind: "bold", text: "1%" },
      { kind: "text", text: " cut-off " },
      { kind: "pmid", pmid: 123 },
      { kind: "text", text: " works." },
    ]);
  });
});

describe("turnsOf", () => {
  it("pairs stored questions with their answers", () => {
    const message = (role: string, content: string, outcome: string | null = null) => ({
      message_id: content, role, content, trace: [], citations: [], outcome, cost_usd: 0.002, latency_ms: 1200, created_at: "2026-11-12T10:00:00Z",
    });
    const conversation = {
      conversation_id: "c", title: "t", created_at: "2026-11-12T10:00:00Z",
      messages: [message("user", "Q1"), message("assistant", "A1", "answered"), message("user", "Q2"), message("assistant", "No.", "refused")],
    } as Conversation;
    const turns = turnsOf(conversation);
    expect(turns.map((t) => [t.question, t.text, t.refusal])).toEqual([["Q1", "A1", null], ["Q2", "No.", "No."]]);
    expect(turns.every((t) => t.done && t.stage === null)).toBe(true);
  });
});
