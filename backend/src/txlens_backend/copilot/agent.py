"""The copilot as a LangGraph state machine.

    screen ──refuse──────────────────────────────▶ refuse ─▶ END
       │
       ▼
     plan ──answer──▶ answer ─▶ END
       │  ▲   └─refuse──▶ refuse
       ▼  │
       act (one tool)

- screen: deterministic patterns first, then the model's classifier.
- plan: the model picks ONE schema-validated action; at most MAX_TOOL_STEPS tools per question,
  and an identical repeated call ends planning.
- act: runs the tool (guards, suppression, audit inside the tool).
- answer: with retrieved abstracts, quote-grounded claims checked in code (grounding.py);
  otherwise the model's text, streamed through the AnswerFilter sentence by sentence.
Progress is sent as custom stream events (status, tool, token, citations, refusal), which the
API forwards to the browser as server-sent events.
"""

from typing import Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from pydantic import TypeAdapter

from txlens_backend.copilot import actions, grounding, prompts, restricted, screen, tools
from txlens_backend.copilot.answer_filter import UNSUPPORTED, AnswerFilter, numbers_in
from txlens_backend.copilot.llm import CopilotLlm

MAX_TOOL_STEPS = 5
DATA_TOOLS = frozenset({"preview_cohort", "run_survival", "sql_query"})
ACTION = TypeAdapter[actions.ToolAction | actions.Answer | actions.Refuse](actions.AnyAction)
LABELS = {"concept_search": "searched concepts", "preview_cohort": "built cohort",
          "run_survival": "ran survival", "sql_query": "ran SQL",
          "literature_search": "searched literature"}  # fmt: skip


class State(TypedDict, total=False):
    """What the graph knows about one question."""

    question: str
    history: list[tuple[str, str]]
    kind: str
    refusal: actions.RefusalReason | None
    steps: list[tuple[dict[str, Any], tools.ToolResult]]
    next: dict[str, Any]
    answer: str
    outcome: str
    filter: dict[str, Any]


class Agent:
    """One question's graph: its LLM calls and its tools."""

    def __init__(self, llm: CopilotLlm, ctx: tools.ToolContext) -> None:
        self.llm, self.ctx = llm, ctx

    def screen(self, state: State) -> State:
        """Refuse clear-cut cases by pattern; otherwise ask the classifier."""
        get_stream_writer()({"type": "status", "stage": "screening"})
        reason = screen.screen(state["question"])
        if reason:
            return {"refusal": reason}
        messages = prompts.classify_messages(state["question"])
        verdict = self.llm.structured(actions.Screening, messages, "classify")
        if verdict.kind == "refuse":
            return {"refusal": verdict.reason or "out_of_scope"}
        return {"kind": verdict.kind}

    def plan(self, state: State) -> State:
        """The next action (answer once enough is known or the step limit is reached)."""
        steps = state.get("steps", [])
        if len(steps) >= MAX_TOOL_STEPS:
            return {"next": {"tool": "answer"}}
        get_stream_writer()({"type": "status", "stage": "planning"})
        messages = prompts.plan_messages(state["question"], state.get("history", []), steps)
        action = self.llm.structured(actions.Step, messages, "plan").action
        chosen = action.model_dump(mode="json")
        if any(done == chosen for done, _ in steps):
            return {"next": {"tool": "answer"}}
        if isinstance(action, actions.Refuse):
            return {"next": chosen, "refusal": action.reason}
        return {"next": chosen}

    def act(self, state: State) -> State:
        """Run the planned tool and report it."""
        action = ACTION.validate_python(state["next"])
        assert not isinstance(action, actions.Answer | actions.Refuse)
        result = tools.run(self.ctx, action)
        get_stream_writer()({
            "type": "tool", "step": len(state.get("steps", [])) + 1, "tool": result.tool,
            "label": LABELS[result.tool], "args": result.args, "ok": result.ok,
            "error": result.error, "sql": result.sql, "display": result.display,
            "notices": result.notices, "latency_ms": result.latency_ms,
        })  # fmt: skip
        return {"steps": [*state.get("steps", []), (state["next"], result)]}

    def answer(self, state: State) -> State:
        """Write the answer: quote-grounded claims when abstracts were retrieved, otherwise
        streamed text through the sentence filter."""
        get_stream_writer()({"type": "status", "stage": "answering"})
        results = [r for _, r in state.get("steps", [])]
        if any(r.sources for r in results):
            return self.grounded_answer(state, results)
        return self.streamed_answer(state, results)

    def grounded_answer(self, state: State, results: list[tools.ToolResult]) -> State:
        """Claims with verbatim quotes; each quote is checked in the abstract it cites."""
        write = get_stream_writer()
        docs = {d.pmid: d for r in results for d in r.sources}
        citations = {c.pmid: c for r in results for c in r.citations}
        data_ok = any(r.ok and r.tool in DATA_TOOLS for r in results)
        messages = prompts.answer_messages(state["question"], state.get("history", []), results,
                                           grounded=True)  # fmt: skip
        answer = self.llm.structured(grounding.GroundedAnswer, messages, "answer-grounded")
        shown: list[str] = []
        quotes: dict[int, list[str]] = {}
        problems: list[str] = []
        names = 0
        for claim in answer.claims:
            checked = grounding.check(claim, docs, data_allowed=data_ok)
            if checked.text is None:
                problems.append(checked.problem or "unsupported")
                continue
            text, replaced = restricted.neutralize(grounding.render(checked.text, checked.quotes))
            names += replaced
            for q in checked.quotes:
                quotes.setdefault(q.pmid, []).append(q.quote)
            shown.append(text)
            write({"type": "token", "text": text + "\n"})
        if not shown and not data_ok:
            shown.append(UNSUPPORTED)
            write({"type": "token", "text": UNSUPPORTED + "\n"})
        if answer.note:
            note, replaced = restricted.neutralize(f"Note: {answer.note.strip()}")
            names += replaced
            shown.append(note)
            write({"type": "token", "text": note})
        cited = [citations[p].model_copy(update={"quotes": quotes[p]}).model_dump()
                 for p in sorted(quotes) if p in citations]  # fmt: skip
        write({"type": "citations", "citations": cited})
        return {"answer": "\n".join(shown).strip(), "outcome": "answered", "filter": {
            "grounded": True, "claims": len(answer.claims), "sentences": len(answer.claims),
            "dropped_uncited": len(problems), "drop_reasons": problems, "invalid_citations": 0,
            "names_replaced": names, "cited": sorted(quotes), "ungrounded_numbers": [],
        }}  # fmt: skip

    def streamed_answer(self, state: State, results: list[tools.ToolResult]) -> State:
        """Stream the answer, sentence by sentence, through the filter (data answers)."""
        write = get_stream_writer()
        citations = {c.pmid: c for r in results for c in r.citations}
        answer_filter = AnswerFilter(
            set(citations),
            # failed calls (e.g. rejected SQL) give no data: they do not excuse uncited claims
            literature_only=bool(citations)
            and not any(r.ok and r.tool in DATA_TOOLS for r in results),
            known_numbers=numbers_in([r.data for r in results if r.ok]),
        )  # fmt: skip
        messages = prompts.answer_messages(state["question"], state.get("history", []), results)
        shown: list[str] = []
        for piece in self.llm.stream(messages, "answer"):
            for sentence in answer_filter.feed(piece):
                shown.append(sentence)
                write({"type": "token", "text": sentence})
        for sentence in answer_filter.finish():
            shown.append(sentence)
            write({"type": "token", "text": sentence})
        cited = [citations[p].model_dump() for p in sorted(answer_filter.stats.cited)]
        write({"type": "citations", "citations": cited})
        stats = answer_filter.stats
        return {"answer": "".join(shown).strip(), "outcome": "answered", "filter": {
            "grounded": False, "sentences": stats.sentences,
            "dropped_uncited": stats.dropped_uncited, "invalid_citations": stats.invalid_citations,
            "names_replaced": stats.names_replaced, "cited": sorted(stats.cited),
            "ungrounded_numbers": stats.ungrounded_numbers,
        }}  # fmt: skip

    def refuse(self, state: State) -> State:
        """A fixed, polite refusal for the reason found."""
        reason = state.get("refusal") or "out_of_scope"
        text = screen.REFUSALS[reason]
        get_stream_writer()({"type": "refusal", "reason": reason, "text": text})
        return {"answer": text, "outcome": "refused"}

    def graph(self) -> Any:
        """The compiled graph."""
        graph = StateGraph(State)
        for name in ("screen", "plan", "act", "answer", "refuse"):
            graph.add_node(name, getattr(self, name))
        graph.add_edge(START, "screen")
        graph.add_conditional_edges("screen", lambda s: "refuse" if s.get("refusal") else "plan")
        graph.add_conditional_edges("plan", lambda s: {"answer": "answer", "refuse": "refuse"}
                                    .get(s["next"]["tool"], "act"))  # fmt: skip
        graph.add_edge("act", "plan")
        graph.add_edge("answer", END)
        graph.add_edge("refuse", END)
        return graph.compile()
