"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, MessageSquarePlus, Send } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent, type KeyboardEvent } from "react";

import { AnswerBody } from "@/components/copilot/answer";
import { ErrorState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { api, unwrap } from "@/lib/api/client";
import { isApiError } from "@/lib/api/errors";
import { applyEvent, askCopilot, newTurn, STAGE_LABEL, turnsOf, type Turn } from "@/lib/copilot";
import { formatDate } from "@/lib/format";
import { keys, useConversation, useConversations, useCreateConversation } from "@/lib/queries";


type Live = { conversationId: string | null; turns: Turn[] };

/** Chat with the copilot: questions stream back with the tools used, charts and sources. */
export function CopilotView() {
  const params = useSearchParams();
  const router = useRouter();
  const client = useQueryClient();
  const selected = params.get("c");
  const conversations = useConversations();
  const stored = useConversation(selected);
  const create = useCreateConversation();
  const [live, setLive] = useState<Live>({ conversationId: null, turns: [] });
  const [question, setQuestion] = useState("");
  // Answers to these are cached, so they work in the public demo too (no API key there).
  const suggestions = useQuery({
    queryKey: ["copilot", "suggestions"],
    queryFn: async () => unwrap(await api.GET("/copilot/suggestions")),
    staleTime: Infinity,
  });
  const [busy, setBusy] = useState(false);

  const turns = [
    ...(stored.data ? turnsOf(stored.data) : []),
    ...(live.conversationId === selected ? live.turns : []),
  ];
  const updateLast = (update: (turn: Turn) => Turn) =>
    setLive((prev) => ({ ...prev, turns: prev.turns.map((t, i) => (i === prev.turns.length - 1 ? update(t) : t)) }));

  async function ask(text: string) {
    const q = text.trim();
    if (q.length < 2 || busy) return;
    setBusy(true);
    setQuestion("");
    let id = selected;
    try {
      if (!id) {
        id = (await create.mutateAsync(q.slice(0, 80))).conversation_id;
        router.replace(`/copilot?c=${id}`);
      }
      const conversationId = id;
      setLive((prev) => ({ conversationId, turns: [...(prev.conversationId === conversationId ? prev.turns : []), newTurn(q)] }));
      await askCopilot(conversationId, q, (event) => updateLast((turn) => applyEvent(turn, event)));
      // The stored conversation now holds this turn: show it from there.
      await client.invalidateQueries({ queryKey: keys.conversation(conversationId) });
      setLive({ conversationId, turns: [] });
      void client.invalidateQueries({ queryKey: keys.conversations });
    } catch (error) {
      const message = isApiError(error) ? error.message : "The copilot could not be reached.";
      updateLast((turn) => ({ ...turn, error: message, done: true, stage: null }));
    } finally {
      setBusy(false);
    }
  }

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void ask(question);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void ask(question);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Research copilot</h1>
        <p className="text-sm text-muted-foreground">
          Answers from your center&apos;s data as de-identified aggregates (counts from 1 to 10 hidden) and from
          open-access transplant abstracts, showing the tools it used and its sources. Synthetic data. Not clinical advice.
        </p>
      </div>
      <div className="grid gap-4 lg:grid-cols-[16rem_minmax(0,1fr)]">
        <Card className="h-fit">
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Conversations</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <Button asChild variant="outline" size="sm" className="w-full">
              <Link href="/copilot"><MessageSquarePlus aria-hidden className="size-4" /> New conversation</Link>
            </Button>
            {conversations.isPending && <Skeleton className="h-24 w-full" />}
            {conversations.isError && <ErrorState error={conversations.error} onRetry={() => conversations.refetch()} />}
            <ul className="space-y-1 text-sm">
              {conversations.data?.map((c) => (
                <li key={c.conversation_id}>
                  <Link
                    href={`/copilot?c=${c.conversation_id}`}
                    aria-current={c.conversation_id === selected ? "page" : undefined}
                    className="block rounded-md px-2 py-1 hover:bg-muted aria-[current=page]:bg-muted aria-[current=page]:font-medium"
                  >
                    <span className="line-clamp-2">{c.title}</span>
                    <span className="text-xs text-muted-foreground">{formatDate(c.created_at)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="space-y-4 pt-6">
            {stored.isError && <ErrorState error={stored.error} onRetry={() => stored.refetch()} />}
            {selected && stored.isPending && <Skeleton className="h-40 w-full" />}
            <div role="log" aria-live="polite" aria-label="Conversation" className="space-y-6">
              {turns.map((turn, i) => (
                <article key={i} className="space-y-3">
                  <p className="ml-auto w-fit max-w-[85%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">{turn.question}</p>
                  <div className="space-y-2 rounded-lg border p-3">
                    <AnswerBody turn={turn} />
                    {turn.stage && (
                      <p className="flex items-center gap-2 text-xs text-muted-foreground">
                        <Loader2 aria-hidden className="size-3.5 animate-spin" />{STAGE_LABEL[turn.stage] ?? "Working…"}
                      </p>
                    )}
                    {turn.done && turn.latencyMs !== null && (
                      <p className="text-xs text-muted-foreground">
                        {(turn.latencyMs / 1000).toFixed(1)} s · ${(turn.costUsd ?? 0).toFixed(4)}
                      </p>
                    )}
                  </div>
                </article>
              ))}
            </div>

            <div className="space-y-2">
              <p className="text-sm text-muted-foreground">Suggested questions:</p>
              <div className="flex flex-wrap gap-2">
                {(suggestions.data ?? []).map((suggestion) => (
                  <Button key={suggestion} type="button" variant="outline" size="sm" className="h-auto whitespace-normal text-left" onClick={() => void ask(suggestion)} disabled={busy}>
                    {suggestion}
                  </Button>
                ))}
              </div>
            </div>

            <form onSubmit={submit} className="space-y-2 border-t pt-4">
              <Label htmlFor="copilot-question">Your question</Label>
              <Textarea
                id="copilot-question"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={onKeyDown}
                maxLength={2000}
                rows={3}
                placeholder="e.g. How many heart recipients had a DSA above 2000 MFI in the first year?"
              />
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs text-muted-foreground">Enter to send, Shift+Enter for a new line.</p>
                <Button type="submit" disabled={busy || question.trim().length < 2}>
                  {busy ? <Loader2 aria-hidden className="size-4 animate-spin" /> : <Send aria-hidden className="size-4" />}
                  Ask
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
