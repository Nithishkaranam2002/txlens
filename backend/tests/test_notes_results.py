"""Note NLP results against a real database (RLS on): saving, OMOP rows, reloads, workflow.

The LLM is faked. The Temporal test is skipped without the test server unless
TXLENS_REQUIRE_DB=1 (CI).
"""

import asyncio
import json
import os
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from document_testutils import MODEL, SNOMED_IDS, FakeCompletion
from sqlalchemy import Engine, text
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from txlens_backend.config import Settings
from txlens_backend.db import tenant_transaction
from txlens_backend.documents.llm import LiteLlmExtractor
from txlens_backend.notes.nlp import Mention, NoteNlp
from txlens_backend.notes.results import save_note, unchanged
from txlens_backend.notes.vocabulary import Term, build
from txlens_backend.workflows.biopsy import TASK_QUEUE
from txlens_backend.workflows.notes import NoteActivities, NoteNlpBatchWorkflow, NotesBatchRequest

pytestmark = pytest.mark.db

REJECTION = SNOMED_IDS["236574008"]  # TEST ONLY standard Condition concepts
ATN = SNOMED_IDS["35455006"]
BORDERLINE = 2_000_000_002  # TxLens local, non-standard
NOTE_TEXT = (
    "CLINIC NOTE\n\nHPI: Rejection on biopsy. Denies ATN, graft tenderness. "
    "Rejection again.\nFHx: Mother with rejection.\n"
)


@pytest.fixture
def notes(clinic: None, documents_owner: Engine) -> Iterator[list[str]]:
    """Two de-identified center_a notes of person 1 (removed afterwards)."""
    sources = [f"TEST-{uuid.uuid4().hex[:8]}" for _ in range(2)]
    with documents_owner.begin() as conn:
        for i, source in enumerate(sources):
            conn.execute(
                text(
                    "INSERT INTO cdm.note (tenant_id, note_id, person_id, note_date, "
                    "note_type_concept_id, note_class_concept_id, note_text, "
                    "encoding_concept_id, language_concept_id, note_source_value) VALUES "
                    "('center_a', :i, 1, :d, 0, 0, :t, 0, 0, :s)"
                ),
                {"i": 700_000 + int(source[5:], 16) % 100_000 * 2 + i, "d": date(2024, 6, 1),
                 "t": NOTE_TEXT, "s": source},
            )  # fmt: skip
    yield sources
    with documents_owner.begin() as conn:
        for statement in (
            "DELETE FROM cdm.condition_occurrence WHERE condition_occurrence_id IN "
            "(SELECT cdm_id FROM app.note_cdm_row WHERE note_source_value = ANY(:s))",
            "DELETE FROM cdm.observation WHERE observation_id IN "
            "(SELECT cdm_id FROM app.note_cdm_row WHERE note_source_value = ANY(:s))",
            "DELETE FROM cdm.note_nlp WHERE note_nlp_id IN "
            "(SELECT mention_id FROM app.note_mention WHERE note_source_value = ANY(:s))",
            "DELETE FROM app.note_processed WHERE note_source_value = ANY(:s)",
            "DELETE FROM cdm.note WHERE note_source_value = ANY(:s)",
        ):
            conn.execute(text(statement), {"s": sources})


def mention(word: str, concept: int, *, occurrence: int = 0, method: str = "curated",
            confidence: float = 1.0, **kw: object) -> Mention:  # fmt: skip
    start = NOTE_TEXT.lower().find(word.lower())
    for _ in range(occurrence):
        start = NOTE_TEXT.lower().find(word.lower(), start + 1)
    values: dict[str, object] = {"negated": False, "temporality": "current",
                                 "experiencer": "patient"} | kw  # fmt: skip
    return Mention(start, start + len(word), NOTE_TEXT[start : start + len(word)], "condition",
                   concept, bool(values["negated"]), values["temporality"],  # type: ignore[arg-type]
                   values["experiencer"], "history_of_present_illness", start, start + len(word),  # type: ignore[arg-type]
                   method, confidence)  # fmt: skip


MENTIONS = [
    mention("Rejection", REJECTION),
    mention("ATN", ATN, negated=True),
    mention("graft tenderness", 0, method="llm", confidence=0.5),
    mention("Rejection", REJECTION, occurrence=1),
    mention("rejection", REJECTION, occurrence=2, experiencer="family"),
    mention("biopsy", BORDERLINE, method="vocabulary"),  # stand-in for a local concept
]


def stored(owner: Engine, source: str) -> dict[str, list[tuple[object, ...]]]:
    with owner.connect() as conn:
        nlp = conn.execute(text(
            "SELECT lexical_variant, note_nlp_concept_id, note_nlp_source_concept_id, "
            "term_exists, term_modifiers FROM cdm.note_nlp WHERE note_nlp_id IN "
            "(SELECT mention_id FROM app.note_mention WHERE note_source_value = :s) "
            "ORDER BY note_nlp_id"), {"s": source}).all()  # fmt: skip
        conditions = conn.execute(text(
            "SELECT c.condition_concept_id, c.condition_start_date, c.condition_type_concept_id, "
            "c.condition_source_value, r.mentions FROM cdm.condition_occurrence c "
            "JOIN app.note_cdm_row r ON r.cdm_id = c.condition_occurrence_id "
            "WHERE r.note_source_value = :s"), {"s": source}).all()  # fmt: skip
        histories = conn.execute(text(
            "SELECT o.observation_concept_id, o.value_as_concept_id, o.observation_date, "
            "o.observation_type_concept_id, r.mentions FROM cdm.observation o "
            "JOIN app.note_cdm_row r ON r.cdm_id = o.observation_id "
            "WHERE r.note_source_value = :s"), {"s": source}).all()  # fmt: skip
    return {"nlp": [tuple(r) for r in nlp], "conditions": [tuple(r) for r in conditions],
            "histories": [tuple(r) for r in histories]}  # fmt: skip


def test_every_mention_reaches_note_nlp_and_only_patient_conditions_reach_cdm(
    notes: list[str], documents_engine: Engine, documents_owner: Engine
) -> None:
    with tenant_transaction(documents_engine, "center_a") as conn:
        saved = save_note(conn, notes[0], NOTE_TEXT, MENTIONS, "txlens-test", 0.70)
    assert (saved.mentions, saved.review, saved.conditions, saved.histories) == (6, 1, 1, 0)
    rows = stored(documents_owner, notes[0])
    assert len(rows["nlp"]) == 6
    by_text = {str(r[0]): r for r in rows["nlp"]}
    assert by_text["ATN"][1:4] == (ATN, 0, "N")  # negated
    assert by_text["biopsy"][1:3] == (0, BORDERLINE)  # local concept: source only
    assert "method=llm" in str(by_text["graft tenderness"][4])
    assert "status=review" in str(by_text["graft tenderness"][4])
    # One condition for the two current patient mentions; family and negated are left out.
    assert rows["conditions"] == [(REJECTION, date(2024, 6, 1), 32858, "Rejection", 2)]


def test_past_conditions_become_history_of_event_observations(
    notes: list[str], documents_engine: Engine, documents_owner: Engine
) -> None:
    past = [
        mention("Rejection", REJECTION, temporality="historical"),
        mention("Rejection", REJECTION, occurrence=1, temporality="historical"),
        mention("ATN", ATN, temporality="historical", negated=True),  # denied: no history
        mention("rejection", REJECTION, occurrence=2, temporality="historical",
                experiencer="family"),
    ]  # fmt: skip
    with tenant_transaction(documents_engine, "center_a") as conn:
        saved = save_note(conn, notes[0], NOTE_TEXT, past, "txlens-test", 0.70)
    assert (saved.conditions, saved.histories) == (0, 1)
    rows = stored(documents_owner, notes[0])
    assert rows["conditions"] == []
    # History of event (1340204), valued with the condition, dated at the note, type NLP.
    assert rows["histories"] == [(1340204, REJECTION, date(2024, 6, 1), 32858, 2)]
    with tenant_transaction(documents_engine, "center_a") as conn:  # a pipeline reload
        conn.execute(text("DELETE FROM cdm.observation WHERE observation_id >= 2100000000"))
        conn.execute(text("SELECT app.load_note_nlp_results()"))
    assert stored(documents_owner, notes[0])["histories"] == rows["histories"]


def test_saving_again_replaces_rows_and_unchanged_notes_are_skipped(
    notes: list[str], documents_engine: Engine, documents_owner: Engine
) -> None:
    for _ in range(2):
        with tenant_transaction(documents_engine, "center_a") as conn:
            save_note(conn, notes[0], NOTE_TEXT, MENTIONS[:2], "txlens-test", 0.70)
    assert len(stored(documents_owner, notes[0])["nlp"]) == 2
    with tenant_transaction(documents_engine, "center_a") as conn:
        texts = {notes[0]: NOTE_TEXT, notes[1]: NOTE_TEXT}
        assert unchanged(conn, texts, "txlens-test") == {notes[0]}
        assert unchanged(conn, texts, "another-system") == set()
        assert unchanged(conn, {notes[0]: NOTE_TEXT + "edited"}, "txlens-test") == set()


def test_results_survive_a_reload_and_stay_in_their_tenant(
    notes: list[str], documents_engine: Engine, documents_owner: Engine
) -> None:
    with tenant_transaction(documents_engine, "center_a") as conn:
        save_note(conn, notes[0], NOTE_TEXT, MENTIONS, "txlens-test", 0.70)
    before = stored(documents_owner, notes[0])
    with tenant_transaction(documents_engine, "center_a") as conn:
        # What the pipeline load does: empty the tenant's cdm tables, reload, copy back.
        conn.execute(text("DELETE FROM cdm.note_nlp"))
        conn.execute(text("DELETE FROM cdm.condition_occurrence WHERE condition_occurrence_id "
                          ">= 2100000000"))  # fmt: skip
        conn.execute(text("SELECT app.load_note_nlp_results()"))
    assert stored(documents_owner, notes[0]) == before
    with tenant_transaction(documents_engine, "center_b") as conn:
        seen = conn.execute(text("SELECT count(*) FROM app.note_mention")).scalar_one()
        assert seen == 0


def test_batch_workflow_processes_notes_with_the_fallback_and_skips_them_next_time(
    notes: list[str], documents_engine: Engine, documents_owner: Engine
) -> None:
    vocabulary = build([Term("rejection", REJECTION, "condition", True),
                        Term("atn", ATN, "condition", True)], [])  # fmt: skip
    fake = FakeCompletion({"findings": [
        {"quote": "graft tenderness", "category": "condition", "negated": True,
         "temporality": "current", "experiencer": "patient"}]})  # fmt: skip
    activities = NoteActivities(
        documents_engine, Settings(llm_budget_usd=1.0),
        LiteLlmExtractor(MODEL, completion=fake), NoteNlp(vocabulary),
    )  # fmt: skip
    results = []

    async def run() -> None:
        try:
            env = await WorkflowEnvironment.start_time_skipping()
        except Exception as error:  # the test server could not be downloaded or started
            if os.environ.get("TXLENS_REQUIRE_DB") == "1":
                raise
            pytest.skip(f"Temporal test server unavailable: {error}")
        async with env:
            with ThreadPoolExecutor(max_workers=2) as pool:
                async with Worker(env.client, task_queue=TASK_QUEUE,
                                  workflows=[NoteNlpBatchWorkflow],
                                  activities=activities.all(), activity_executor=pool):  # fmt: skip
                    for _ in range(2):
                        results.append(await env.client.execute_workflow(
                            NoteNlpBatchWorkflow.run,
                            NotesBatchRequest(f"test-{uuid.uuid4()}", ["center_a"]),
                            id=f"test-{uuid.uuid4()}", task_queue=TASK_QUEUE,
                        ))  # fmt: skip

    with documents_owner.begin() as conn:  # only this test's notes are in the tenant
        others = conn.execute(text("SELECT count(*) FROM cdm.note WHERE tenant_id = 'center_a' "
                                   "AND NOT note_source_value = ANY(:s)"), {"s": notes},
                              ).scalar_one()  # fmt: skip
    if others:
        pytest.skip("center_a has other notes in this database")
    asyncio.run(run())
    first, second = results
    assert (first.processed, first.skipped, second.processed, second.skipped) == (2, 0, 0, 2)
    assert first.conditions == 2 and first.review == 2  # graft tenderness: concept 0
    assert len(fake.calls) == 1  # the same sentence in the second note came from the cache
    with documents_owner.connect() as conn:
        audited = conn.execute(text(
            "SELECT count(*) FROM app.audit_log WHERE action = 'note_nlp_save' "
            "AND object_id = ANY(:s)"), {"s": notes}).scalar_one()  # fmt: skip
    assert audited == 2


def decide(owner: Engine, key: str, action: str, final: dict[str, str] | None = None) -> None:
    """A reviewer decision on one span, as the review API stores it."""
    with owner.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app.review_decision (tenant_id, item_type, item_key, action, "
                "reason_code, changes, reviewer_id, queued_at) VALUES ('center_a', "
                "'note_mention', :k, :a, :r, CAST(:c AS jsonb), "
                "'00000000-0000-0000-0000-0000000000a1', now())"
            ),
            {"k": key, "a": action, "r": None if action == "approve" else "wrong_concept",
             "c": json.dumps({"final": final or {}})},
        )  # fmt: skip


def test_reprocessing_a_note_keeps_reviewer_decisions(
    notes: list[str], reviewer: None, documents_engine: Engine, documents_owner: Engine
) -> None:
    tenderness, first = MENTIONS[2], MENTIONS[0]
    decide(documents_owner, f"{notes[0]}:{tenderness.start}-{tenderness.end}", "edit",
           {"concept_id": str(ATN), "negated": "false", "temporality": "current",
            "experiencer": "patient"})  # fmt: skip
    decide(documents_owner, f"{notes[0]}:{first.start}-{first.end}", "reject")
    with tenant_transaction(documents_engine, "center_a") as conn:
        saved = save_note(conn, notes[0], NOTE_TEXT, MENTIONS, "txlens-test", 0.70)
    assert saved.review == 0  # the LLM finding was reviewed: no longer waiting
    with documents_owner.connect() as conn:
        status: dict[str, str] = dict(conn.execute(text(
            "SELECT lexical_variant || start_offset, status FROM app.note_mention "
            "WHERE note_source_value = :s"), {"s": notes[0]}).all())  # fmt: skip
    assert status[f"Rejection{first.start}"] == "rejected"
    assert status[f"graft tenderness{tenderness.start}"] == "accepted"
    conditions = {r[0]: r[4] for r in stored(documents_owner, notes[0])["conditions"]}
    assert conditions == {REJECTION: 1, ATN: 1}  # one rejection left; the edit added ATN
