"""The review queue API against a real database (RLS on): order, evidence per role, decisions,
the curated-mapping history, metrics, tenant isolation and the audit trail."""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date

import pytest
from api_testutils import login
from document_testutils import (
    KIDNEY_OCR_LINES,
    report_lines_for,
    seed_document,
    write_report_pdf,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from note_testutils import SURROGATE, NoteCondition, remove_note_condition, seed_note_condition
from sqlalchemy import Engine, text

from txlens_backend.api.deps import Role
from txlens_backend.documents.service import QC_REASON

pytestmark = pytest.mark.db
CURATED = ("TXLENS_NOTE_ABBREV", "test-abbrev")


@dataclass(frozen=True)
class Seeded:
    """What each test of this module puts in the API database (fresh per test)."""

    rejection_doc: str  # center_a, linked to a patient, diagnosis TCMR
    qc_doc: str  # center_a, QC sample, no rejection
    other_center_doc: str  # center_b
    mention_key: str  # center_a note mention in review
    note: NoteCondition
    concepts: tuple[int, int]  # two standard condition concepts
    gap: str  # center_a condition text


def owner_run(owner: Engine, sql: str, **params: object) -> None:
    with owner.begin() as conn:
        conn.execute(text(sql), params)


@pytest.fixture
def seeded(
    api_owner: Engine, api_app_engine: Engine, api_app: FastAPI,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Seeded]:  # fmt: skip
    root = tmp_path_factory.mktemp("review_documents")
    (root / "pathology").mkdir()
    write_report_pdf(root / "pathology" / "review.pdf")
    settings = api_app.state.ctx.settings
    before, settings.documents_root = settings.documents_root, root
    with api_owner.begin() as conn:
        patient = conn.execute(text(
            "SELECT i.person_id, i.mrn, i.given_name, i.family_name, i.birth_date "
            "FROM app.patient_identity i WHERE i.tenant_id = 'center_a' "
            "ORDER BY i.person_id LIMIT 1")).one()  # fmt: skip
        lines = report_lines_for(patient.mrn, patient.given_name or "Ana",
                                 patient.family_name or "Doe", patient.birth_date,
                                 patient.birth_date)  # fmt: skip
        rejection = seed_document(conn, "center_a", lines, "in_review", patient.person_id,
                                  day=date(2024, 5, 6), body_top=300).document_id  # fmt: skip
        conn.execute(text("UPDATE app.document SET source_path = 'pathology/review.pdf' "
                          "WHERE document_id = :d"), {"d": rejection})  # fmt: skip
        qc = seed_document(conn, "center_a", KIDNEY_OCR_LINES, "in_review").document_id
        conn.execute(text(
            "UPDATE app.document SET review_reasons = CAST(:r AS jsonb), "
            "updated_at = now() - interval '1 day' WHERE document_id = :d"),
            {"r": json.dumps([QC_REASON]), "d": qc})  # fmt: skip
        conn.execute(text("UPDATE app.extracted_field SET value = 'no_rejection', "
                          "extracted_value = 'no_rejection' WHERE document_id = :d "
                          "AND field = 'diagnosis'"), {"d": qc})  # fmt: skip
        other = seed_document(conn, "center_b", KIDNEY_OCR_LINES, "in_review").document_id
        concepts = tuple(conn.execute(text(
            "SELECT concept_id FROM vocab.concept WHERE standard_concept = 'S' "
            "AND domain_id = 'Condition' ORDER BY concept_id LIMIT 2")).scalars())  # fmt: skip
        conn.execute(text(
            "INSERT INTO vocab.source_to_concept_map (source_code, source_concept_id, "
            "source_vocabulary_id, source_code_description, target_concept_id, "
            "target_vocabulary_id, valid_start_date, valid_end_date) VALUES (:c, 0, :v, "
            "'test [curated by AI assistant, pending clinical review]', :t, 'SNOMED', "
            "'2026-11-11', '2099-12-31')"),
            {"v": CURATED[0], "c": CURATED[1], "t": concepts[0]})  # fmt: skip
        conn.execute(text(
            "INSERT INTO app.mapping_gap (tenant_id, source_value, reason, rows, "
            "suggested_concept_id, suggestion_score, review_reason) VALUES ('center_a', "
            "'Odd condition text', 'unmapped', 3, :s, 0.8, 'low_score')"),
            {"s": concepts[0]})  # fmt: skip
    note = seed_note_condition(api_owner, api_app_engine)
    start = note_text_start()
    key = f"{note.sources[0]}:{start}-{start + len('rejection')}"
    owner_run(api_owner, "UPDATE app.note_mention SET status = 'review', confidence = 0.5 "
              "WHERE note_source_value = :s", s=note.sources[0])  # fmt: skip
    yield Seeded(rejection, qc, other, key, note, (concepts[0], concepts[1]),
                 "Odd condition text")  # fmt: skip
    settings.documents_root = before
    remove_note_condition(api_owner, note)
    with api_owner.begin() as conn:
        for sql in (
            "DELETE FROM app.labelled_example",
            "DELETE FROM app.review_decision",
            "DELETE FROM app.curated_mapping_history",
            "DELETE FROM app.mapping_gap",
            "DELETE FROM vocab.source_to_concept_map WHERE source_vocabulary_id = :v",
            "DELETE FROM app.document WHERE document_id = ANY(CAST(:d AS uuid[]))",
        ):
            conn.execute(text(sql), {"v": CURATED[0], "d": [rejection, qc, other]})  # fmt: skip


def note_text_start() -> int:
    from note_testutils import DEID_TEXT

    return DEID_TEXT.index("rejection")


def queue(client: TestClient, **params: str) -> dict[str, object]:
    response = client.get("/review/items", params={"limit": "200"} | params)
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def keys(page: dict[str, object]) -> list[str]:
    return [i["item_key"] for i in page["items"]]  # type: ignore[attr-defined]


def audit_rows(owner: Engine, action: str, object_id: str) -> list[str]:
    with owner.connect() as conn:
        return list(conn.execute(text(
            "SELECT tenant_id FROM app.audit_log WHERE action = :a AND object_id = :o"),
            {"a": action, "o": object_id}).scalars())  # fmt: skip


def test_queue_order_rejection_then_qc_then_the_rest(client: TestClient, seeded: Seeded) -> None:
    login(client, Role.CLINICIAN)
    page = queue(client, type="biopsy_document")
    order = keys(page)
    assert order.index(seeded.rejection_doc) < order.index(seeded.qc_doc)
    item = {i["item_key"]: i for i in page["items"]}  # type: ignore[attr-defined]
    assert item[seeded.rejection_doc]["tier_label"] == "rejection / high severity"
    assert item[seeded.qc_doc]["qc_sample"] and item[seeded.qc_doc]["tier"] == 1
    assert seeded.other_center_doc not in order  # RLS: another center's document
    counts = page["pending_by_type"]
    assert counts["note_mention"] >= 1 and counts["curated_mapping"] >= 1  # type: ignore[index]
    assert counts["condition_mapping"] == 1  # type: ignore[index]


def test_other_centers_see_neither_items_nor_gaps(client: TestClient, seeded: Seeded) -> None:
    login(client, Role.DATA_STEWARD, "center_b")
    page = queue(client)
    assert seeded.rejection_doc not in keys(page) and seeded.gap not in keys(page)
    assert seeded.other_center_doc in keys(page)
    url = f"/review/items/biopsy_document/{seeded.rejection_doc}"
    assert client.get(url).status_code == 404
    assert client.post(url, json={"action": "approve"}).status_code == 404


def test_clinician_sees_the_page_and_the_identified_sentence(
    client: TestClient, seeded: Seeded, api_owner: Engine
) -> None:
    login(client, Role.CLINICIAN)
    doc = client.get(f"/review/items/biopsy_document/{seeded.rejection_doc}").json()
    assert doc["evidence"]["evidence"]["view"] == "full"
    assert doc["evidence"]["evidence"]["page_urls"] and doc["evidence"]["crop_urls"] == {}
    assert audit_action(api_owner, seeded.rejection_doc) == "phi_view"
    mention = client.get(f"/review/items/note_mention/{seeded.mention_key}").json()
    sentence = mention["evidence"]["sentence"]
    start, end = mention["evidence"]["highlight_start"], mention["evidence"]["highlight_end"]
    assert seeded.note.given_name in sentence and sentence[start:end] == "rejection"


def audit_action(owner: Engine, object_id: str) -> str:
    with owner.connect() as conn:
        return str(conn.execute(text(
            "SELECT action FROM app.audit_log WHERE object_id = :o ORDER BY occurred_at DESC "
            "LIMIT 1"), {"o": object_id}).scalar_one())  # fmt: skip


def test_steward_sees_redacted_evidence_crops_and_the_deidentified_sentence(
    client: TestClient, seeded: Seeded, api_owner: Engine
) -> None:
    login(client, Role.DATA_STEWARD)
    response = client.get(f"/review/items/biopsy_document/{seeded.rejection_doc}")
    body = response.text
    doc = response.json()
    assert doc["evidence"]["evidence"]["view"] == "redacted"
    assert doc["evidence"]["evidence"]["page_urls"] == []
    assert seeded.note.given_name not in body
    crops = doc["evidence"]["crop_urls"]
    assert "diagnosis" in crops and "biopsy_date" not in crops  # header line: never a crop
    image = client.get(crops["diagnosis"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    refused = f"/documents/{seeded.rejection_doc}/fields/biopsy_date/crop"
    assert client.get(refused).status_code == 404
    assert client.get(f"/documents/{seeded.rejection_doc}/pages/1").status_code == 403
    assert audit_action(api_owner, seeded.rejection_doc) == "deid_view"
    mention = client.get(f"/review/items/note_mention/{seeded.mention_key}").json()
    assert SURROGATE in mention["evidence"]["sentence"]
    assert seeded.note.given_name not in mention["evidence"]["sentence"]
    curated = client.get(f"/review/items/curated_mapping/{':'.join(CURATED)}").json()
    assert curated["actions"] == []  # stewards may look, not decide


def test_edit_needs_a_reason_and_is_signalled_recorded_and_audited(
    client: TestClient, seeded: Seeded, api_owner: Engine, api_app: FastAPI
) -> None:
    sent: list[object] = []

    class Waiting:
        async def send(self, workflow_id: str, decision: object) -> bool:
            sent.append(decision)
            return True

    before = api_app.state.review_sender
    api_app.state.review_sender = Waiting()
    try:
        login(client, Role.CLINICIAN)
        url = f"/review/items/biopsy_document/{seeded.rejection_doc}"
        edit = {"action": "edit", "changes": {"banff_t": "3"}}
        assert client.post(url, json=edit).status_code == 422  # no reason code
        response = client.post(url, json=edit | {"reason_code": "ocr_error"})
        assert response.json()["status"] == "submitted"
    finally:
        api_app.state.review_sender = before
    assert sent and sent[0].reason_code == "ocr_error"  # type: ignore[attr-defined]
    with api_owner.connect() as conn:
        examples: dict[str, bool] = dict(conn.execute(text(
            "SELECT e.field, e.agrees FROM app.labelled_example e JOIN app.review_decision d "
            "USING (decision_id) WHERE d.item_key = :k"),
            {"k": seeded.rejection_doc}).all())  # fmt: skip
        stored = conn.execute(text(
            "SELECT model_value, reviewer_value FROM app.labelled_example e JOIN "
            "app.review_decision d USING (decision_id) WHERE d.item_key = :k AND "
            "e.field = 'biopsy_date'"), {"k": seeded.rejection_doc}).one()  # fmt: skip
    assert examples["banff_t"] is False and examples["diagnosis"] is True
    assert set(stored) == {"[DATE]"}  # dates never stored in the example set
    assert audit_rows(api_owner, "review_decision", seeded.rejection_doc) == ["center_a"]


def test_editing_a_note_mention_accepts_it_and_rebuilds_its_omop_rows(
    client: TestClient, seeded: Seeded, api_owner: Engine
) -> None:
    login(client, Role.DATA_STEWARD)
    url = f"/review/items/note_mention/{seeded.mention_key}"
    bad = {"action": "edit", "reason_code": "wrong_temporality",
           "changes": {"temporality": "someday"}}  # fmt: skip
    assert client.post(url, json=bad).status_code == 422
    edit = bad | {"changes": {"temporality": "historical"}}
    assert client.post(url, json=edit).json()["status"] == "applied"
    assert client.post(url, json=edit).status_code == 409  # already decided
    with api_owner.connect() as conn:
        status, temporality = conn.execute(text(
            "SELECT status, temporality FROM app.note_mention WHERE note_source_value = :s"),
            {"s": seeded.note.sources[0]}).one()  # fmt: skip
        rows: dict[str, int] = dict(conn.execute(text(
            "SELECT cdm_table, count(*) FROM app.note_cdm_row WHERE note_source_value = :s "
            "GROUP BY cdm_table"), {"s": seeded.note.sources[0]}).all())  # fmt: skip
    # No longer a current condition: a "History of event" observation instead.
    assert (status, temporality, rows) == ("accepted", "historical", {"observation": 1})


def test_curated_mapping_history_is_shared_and_can_be_reverted(
    client: TestClient, seeded: Seeded, api_owner: Engine
) -> None:
    url = f"/review/items/curated_mapping/{':'.join(CURATED)}"
    login(client, Role.DATA_STEWARD)
    assert client.post(url, json={"action": "approve"}).status_code == 403
    login(client, Role.CLINICIAN)
    revert = {"action": "revert", "reason_code": "wrong_concept"}
    assert client.post(url, json=revert).status_code == 409  # nothing to revert yet
    correct = {"action": "edit", "reason_code": "wrong_concept",
               "changes": {"concept_id": str(seeded.concepts[1])}}  # fmt: skip
    assert client.post(url, json=correct).status_code == 200
    detail = client.get(url).json()
    assert detail["evidence"]["label"] == "clinician-reviewed"
    assert detail["evidence"]["concept"]["concept_id"] == seeded.concepts[1]
    assert detail["evidence"]["history"][0]["reviewer_center"] == "center_a"
    login(client, Role.CLINICIAN, "center_b")  # one review counts for every center
    assert ":".join(CURATED) not in keys(queue(client, type="curated_mapping"))
    assert client.post(url, json=revert).status_code == 200
    reverted = client.get(url).json()["evidence"]
    assert reverted["label"] == "AI-curated, pending"
    assert reverted["concept"]["concept_id"] == seeded.concepts[0]
    assert [h["action"] for h in reverted["history"]] == ["correct", "revert"]
    assert sorted(audit_rows(api_owner, "review_decision", ":".join(CURATED))) == [
        "center_a", "center_b"]  # fmt: skip


def test_a_condition_gap_takes_the_suggestion(client: TestClient, seeded: Seeded) -> None:
    login(client, Role.DATA_STEWARD)
    url = f"/review/items/condition_mapping/{seeded.gap}"
    assert client.get(url).json()["evidence"]["concept"]["concept_id"] == seeded.concepts[0]
    assert client.post(url, json={"action": "approve"}).status_code == 200
    assert seeded.gap not in keys(queue(client, type="condition_mapping"))
    assert seeded.gap in keys(queue(client, status="decided"))


@pytest.fixture
def decided(api_owner: Engine) -> Iterator[None]:
    """One center A decision per item type (and a disagreeing banff_t example), so the
    metrics do not depend on which review tests ran before (tests run in random order)."""
    with api_owner.begin() as conn:
        reviewer = conn.execute(
            text("SELECT user_id FROM app.app_user WHERE username = 'clinician.center_a'")
        ).scalar_one()
        ids = list(conn.execute(text(
            "INSERT INTO app.review_decision (tenant_id, item_type, item_key, action, "
            "reason_code, reviewer_id, queued_at) SELECT 'center_a', t, 'metrics-' || t, 'edit', "
            "'ocr_error', :r, now() - interval '2 hours' FROM unnest(CAST(:t AS text[])) AS t "
            "RETURNING decision_id"),
            {"r": reviewer, "t": ["biopsy_document", "note_mention", "curated_mapping"]},
        ).scalars())  # fmt: skip
        conn.execute(text(
            "INSERT INTO app.labelled_example (tenant_id, decision_id, item_type, field, "
            "model_value, reviewer_value, agrees, qc_sample) SELECT 'center_a', decision_id, "
            "item_type, 'banff_t', '2', '3', false, false FROM app.review_decision "
            "WHERE decision_id = :d"), {"d": ids[0]})  # fmt: skip
    yield
    with api_owner.begin() as conn:
        conn.execute(text("DELETE FROM app.labelled_example WHERE decision_id = ANY(:d)"),
                     {"d": ids})  # fmt: skip
        conn.execute(text("DELETE FROM app.review_decision WHERE decision_id = ANY(:d)"),
                     {"d": ids})  # fmt: skip


@pytest.mark.usefixtures("decided")
def test_metrics_count_queue_reviews_and_agreement(client: TestClient, seeded: Seeded) -> None:
    login(client, Role.ADMIN)
    metrics = client.get("/review/metrics").json()
    assert metrics["queue_by_type"]["biopsy_document"] >= 1
    login(client, Role.CLINICIAN)
    metrics = client.get("/review/metrics").json()
    reviewed = {t["item_type"] for t in metrics["time_to_review"]}
    assert {"biopsy_document", "note_mention", "curated_mapping"} <= reviewed
    fields = {(a["item_type"], a["field"]) for a in metrics["agreement"]}
    assert ("biopsy_document", "banff_t") in fields
    login(client, Role.RESEARCHER)
    assert client.get("/review/metrics").status_code == 403
    assert client.get("/review/items").status_code == 403
