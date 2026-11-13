"""Run the note NLP on a tenant's notes and save the results (used by the Temporal workflow).

Notes are read de-identified, under Row-Level Security. A note whose text and NLP system are
unchanged since it was processed is skipped, so re-running a batch costs nothing. Each saved
note gets an audit row (action "note_nlp_save").
"""

from dataclasses import dataclass

from sqlalchemy import Engine, text

from txlens_backend import audit
from txlens_backend.db import tenant_transaction
from txlens_backend.notes.fallback import PROMPT_VERSION, Fallback
from txlens_backend.notes.nlp import NoteNlp
from txlens_backend.notes.results import save_note, unchanged
from txlens_backend.notes.store import read_notes


@dataclass(frozen=True)
class ChunkResult:
    """What one chunk of notes produced."""

    processed: int = 0
    skipped: int = 0
    mentions: int = 0
    review: int = 0
    conditions: int = 0
    llm_cost_usd: float = 0.0
    budget_exhausted: bool = False
    llm_pending: int = 0  # notes saved rules-only: a sentence had no key and no cached answer


def system_name(nlp: NoteNlp, hybrid: bool) -> str:
    """The nlp_system written to note_nlp: rules alone, or rules + the prompt version. Both
    versions are in the name, so changing either one makes the next batch redo every note."""
    return f"{nlp.system}+llm-{PROMPT_VERSION}" if hybrid else nlp.system


def note_source_values(engine: Engine, tenant_id: str) -> list[str]:
    """Every note of the tenant, in a stable order."""
    with tenant_transaction(engine, tenant_id) as conn:
        query = text("SELECT note_source_value FROM cdm.note ORDER BY note_source_value")
        return list(conn.execute(query).scalars())


def process_notes(
    engine: Engine,
    tenant_id: str,
    source_values: list[str],
    nlp: NoteNlp,
    fallback: Fallback | None,
    threshold: float,
    workflow_id: str,
) -> ChunkResult:
    """Extract, then save every changed note of the chunk in one transaction.

    A note counts as hybrid only if the LLM answered every sentence it was asked (the budget
    lasted, and each answer was cached or an API key was set); otherwise it is saved under the
    rules system name, so a later batch processes it again.
    """
    system = system_name(nlp, fallback is not None)
    with tenant_transaction(engine, tenant_id) as conn:
        notes = read_notes(conn, source_values)
        done = unchanged(conn, {n.source_value: n.text for n in notes}, system)
    todo = [n for n in notes if n.source_value not in done]
    found = nlp.extract_many([(n.text, n.organ) for n in todo])
    systems = [system] * len(todo)
    cost_before = fallback.run.cost_usd if fallback else 0.0
    pending = 0
    if fallback is not None:
        for i, note in enumerate(todo):
            offline_before = fallback.run.offline
            if not fallback.run.budget_exhausted:
                found[i] = fallback.resolve(note.text, note.organ, found[i])
            if fallback.run.budget_exhausted or fallback.run.offline > offline_before:
                systems[i] = nlp.system
                pending += fallback.run.offline > offline_before
    mentions = review = conditions = 0
    with tenant_transaction(engine, tenant_id) as conn:
        for note, note_mentions, used in zip(todo, found, systems, strict=True):
            saved = save_note(conn, note.source_value, note.text, note_mentions, used, threshold)
            audit.record(
                conn, audit.RequestInfo(request_id=workflow_id, ip=""), None, "note_nlp_save",
                "note", note.source_value,
                {"system": used, "mentions": saved.mentions, "review": saved.review,
                 "conditions": saved.conditions, "histories": saved.histories},
            )  # fmt: skip
            mentions += saved.mentions
            review += saved.review
            conditions += saved.conditions
    return ChunkResult(
        len(todo), len(done), mentions, review, conditions,
        (fallback.run.cost_usd - cost_before) if fallback else 0.0,
        fallback.run.budget_exhausted if fallback else False, pending,
    )  # fmt: skip
