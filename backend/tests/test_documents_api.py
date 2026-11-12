"""Documents API: timeline results by role, evidence (full vs redacted), page images, review."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from api_testutils import login
from document_testutils import KIDNEY_OCR_LINES, SeededDocument, seed_document, write_report_pdf
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from txlens_backend.api.deps import Role
from txlens_backend.documents.service import ReviewDecision

pytestmark = pytest.mark.db
REVIEW = "/review/items/biopsy_document"


def seeded(documents: list[SeededDocument], tenant: str, status: str) -> SeededDocument:
    return next(d for d in documents if d.tenant_id == tenant and d.status == status)


def biopsy_results(client: TestClient, patient_id: object) -> list[dict[str, Any]]:
    timeline = client.get(f"/patients/{patient_id}/timeline").json()
    return [p["result"] for p in timeline["procedures"] if p["result"]]


def test_clinicians_see_the_result_with_a_link_to_its_evidence(
    client: TestClient, api_documents: list[SeededDocument]
) -> None:
    doc = seeded(api_documents, "center_a", "in_review")
    login(client, Role.CLINICIAN)
    results = biopsy_results(client, doc.person_id)
    mine = next(r for r in results if r["document_id"] == doc.document_id)
    assert mine["status"] == "in_review"
    assert (mine["diagnosis"], mine["grade"], mine["scores"]) == ("TCMR", "IA", {"t": "2"})
    assert mine["evidence_url"] == f"/documents/{doc.document_id}/evidence"


@pytest.mark.parametrize(("role", "links"), [(Role.RESEARCHER, False), (Role.DATA_STEWARD, True)])
def test_deidentified_roles_see_the_result(
    client: TestClient, api_documents: list[SeededDocument], role: Role, links: bool
) -> None:
    login(client, role)
    found = []
    for patient in client.get("/patients", params={"limit": 200}).json()["items"]:
        results = biopsy_results(client, patient["patient_id"])
        found += [r for r in results if r["status"] == "approved" and r["diagnosis"] == "TCMR"]
    assert found, "the approved result must be visible"
    assert all((r["document_id"] is not None) == links for r in found)
    assert all((r["evidence_url"] is not None) == links for r in found)


def test_clinicians_get_full_evidence_with_pages(
    client: TestClient, api_documents: list[SeededDocument], api_owner: Engine
) -> None:
    doc = seeded(api_documents, "center_a", "extracted")
    login(client, Role.CLINICIAN)
    body = client.get(f"/documents/{doc.document_id}/evidence").json()
    assert body["view"] == "full"
    assert body["page_urls"] == [f"/documents/{doc.document_id}/pages/1"]
    fields = {f["field"]: f for f in body["fields"]}
    assert fields["banff_t"]["bbox"] == [10, 20, 30, 40]
    assert fields["banff_t"]["line_text"] == "Banff lesion scores: i2 t2 v0 g0 ptc0 ci1 ct1 cv0"
    with api_owner.connect() as conn:
        action = conn.execute(
            text("SELECT action FROM app.audit_log WHERE object_id = :d ORDER BY audit_id DESC"),
            {"d": doc.document_id},
        ).first()
    assert action is not None and action[0] == "phi_view"


def test_data_stewards_get_redacted_evidence_lines_only(
    client: TestClient, api_documents: list[SeededDocument], api_owner: Engine
) -> None:
    doc = seeded(api_documents, "center_a", "in_review")
    with api_owner.connect() as conn:
        who = conn.execute(
            text(
                "SELECT mrn, given_name, family_name, birth_date FROM app.patient_identity "
                "WHERE person_id = :p"
            ),
            {"p": doc.person_id},
        ).one()
        day = conn.execute(
            text(
                "SELECT procedure_date FROM cdm.procedure_occurrence p JOIN app.document d "
                "ON d.procedure_occurrence_id = p.procedure_occurrence_id "
                "WHERE d.document_id = :d"
            ),
            {"d": doc.document_id},
        ).scalar_one()
    login(client, Role.DATA_STEWARD)
    response = client.get(f"/documents/{doc.document_id}/evidence")
    body = response.json()
    assert (body["view"], body["page_urls"], body["page_sizes"]) == ("redacted", [], [])
    fields = {f["field"]: f for f in body["fields"]}
    assert all(f["bbox"] is None and f["page"] is None for f in body["fields"])
    assert (
        fields["additional_finding"]["line_text"]
        == "Discussed with [NAME] [NAME] on [DATE]; ref [ID]"
    )
    assert fields["biopsy_date"]["line_text"] == "Procedure date: [DATE] Reported: [DATE]"
    assert fields["biopsy_date"]["value"] != day.isoformat()  # shifted like every steward date
    for value in (
        who.mrn,
        who.given_name,
        who.family_name,
        f"{who.birth_date:%m/%d/%Y}",
        day.isoformat(),
        f"{day:%m/%d/%Y}",
    ):
        assert value not in response.text


@pytest.mark.parametrize("role", [Role.RESEARCHER, Role.ADMIN])
def test_other_roles_cannot_see_evidence(
    client: TestClient, api_documents: list[SeededDocument], role: Role
) -> None:
    login(client, role)
    doc = seeded(api_documents, "center_a", "extracted")
    assert client.get(f"/documents/{doc.document_id}/evidence").status_code == 403


@pytest.mark.parametrize(
    "document_id", ["other-center", "not-a-uuid", "00000000-0000-0000-0000-000000000000"]
)
def test_unknown_or_foreign_documents_are_404(
    client: TestClient, api_documents: list[SeededDocument], document_id: str
) -> None:
    if document_id == "other-center":
        document_id = seeded(api_documents, "center_b", "extracted").document_id
    login(client, Role.CLINICIAN)
    assert client.get(f"/documents/{document_id}/evidence").status_code == 404


@pytest.fixture
def documents_on_disk(api_app: FastAPI, tmp_path: Path) -> Iterator[Path]:
    """Point the API at a temporary document store for the duration of a test."""
    settings = api_app.state.ctx.settings
    before = settings.documents_root
    settings.documents_root = tmp_path
    yield tmp_path
    settings.documents_root = before


def unlinked_document(owner: Engine, status: str, source_path: str = "pathology/x.pdf") -> str:
    with owner.begin() as conn:
        doc = seed_document(conn, "center_a", KIDNEY_OCR_LINES, status)
        conn.execute(
            text("UPDATE app.document SET source_path = :s WHERE document_id = :d"),
            {"s": source_path, "d": doc.document_id},
        )
    return doc.document_id


def test_clinicians_get_page_images(
    client: TestClient, api_owner: Engine, documents_on_disk: Path
) -> None:
    (documents_on_disk / "pathology").mkdir()
    write_report_pdf(documents_on_disk / "pathology" / "r.pdf")
    document_id = unlinked_document(api_owner, "extracted", "pathology/r.pdf")
    login(client, Role.CLINICIAN)
    page = client.get(f"/documents/{document_id}/pages/1")
    assert (page.status_code, page.headers["content-type"]) == (200, "image/png")
    assert page.content.startswith(b"\x89PNG")
    assert client.get(f"/documents/{document_id}/pages/2").status_code == 404
    login(client, Role.DATA_STEWARD)
    assert client.get(f"/documents/{document_id}/pages/1").status_code == 403


def test_document_paths_cannot_escape_the_store(
    client: TestClient, api_owner: Engine, documents_on_disk: Path
) -> None:
    (documents_on_disk.parent / "secret.pdf").write_bytes(b"%PDF-1.4")
    document_id = unlinked_document(api_owner, "extracted", "../secret.pdf")
    login(client, Role.CLINICIAN)
    assert client.get(f"/documents/{document_id}/pages/1").status_code == 404


class FakeSender:
    """Records decisions; `waiting` says whether a workflow takes them."""

    def __init__(self, waiting: bool) -> None:
        self.waiting = waiting
        self.sent: list[tuple[str, ReviewDecision]] = []

    async def send(self, workflow_id: str, decision: ReviewDecision) -> bool:
        self.sent.append((workflow_id, decision))
        return self.waiting


@pytest.fixture
def sender(api_app: FastAPI, request: pytest.FixtureRequest) -> Iterator[FakeSender]:
    fake = FakeSender(getattr(request, "param", True))
    before = api_app.state.review_sender
    api_app.state.review_sender = fake
    yield fake
    api_app.state.review_sender = before


def test_a_review_is_signalled_to_the_waiting_workflow(
    client: TestClient, api_owner: Engine, sender: FakeSender
) -> None:
    document_id = unlinked_document(api_owner, "in_review")
    login(client, Role.DATA_STEWARD)
    response = client.post(
        f"{REVIEW}/{document_id}",
        json={"action": "edit", "changes": {"banff_t": "3"}, "note": "t3",
              "reason_code": "ocr_error"},
    )  # fmt: skip
    assert response.json() == {"item_type": "biopsy_document", "item_key": document_id,
                               "status": "submitted"}  # fmt: skip
    workflow_id, decision = sender.sent[0]
    assert workflow_id.startswith("wf-") and decision.changes == {"banff_t": "3"}
    assert decision.reason_code == "ocr_error"
    with api_owner.connect() as conn:
        audited = conn.execute(
            text(
                "SELECT user_id FROM app.audit_log WHERE action = 'document_review_submit' "
                "AND object_id = :d"
            ),
            {"d": document_id},
        ).scalar_one()
    assert str(audited) == decision.reviewer_id


@pytest.mark.parametrize("sender", [False], indirect=True)
def test_without_a_waiting_workflow_the_review_is_applied_directly(
    client: TestClient, api_owner: Engine, sender: FakeSender
) -> None:
    document_id = unlinked_document(api_owner, "in_review")
    login(client, Role.CLINICIAN)
    reject = {"action": "reject", "reason_code": "insufficient_evidence"}
    assert client.post(f"{REVIEW}/{document_id}", json=reject).json()["status"] == "applied"
    with api_owner.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM app.document WHERE document_id = :d"), {"d": document_id}
        ).scalar_one()
    assert status == "rejected"


def test_reviews_need_a_document_in_review_and_valid_edits(
    client: TestClient, api_owner: Engine, sender: FakeSender
) -> None:
    done = unlinked_document(api_owner, "approved")
    waiting = unlinked_document(api_owner, "in_review")
    login(client, Role.DATA_STEWARD)
    assert client.post(f"{REVIEW}/{done}", json={"action": "approve"}).status_code == 409
    bad = {"action": "edit", "changes": {"banff_t": "9"}, "reason_code": "wrong_value"}
    assert client.post(f"{REVIEW}/{waiting}", json=bad).status_code == 422
    no_reason = {"action": "edit", "changes": {"banff_t": "3"}}
    assert client.post(f"{REVIEW}/{waiting}", json=no_reason).status_code == 422
    assert sender.sent == []
    login(client, Role.RESEARCHER)
    assert client.post(f"{REVIEW}/{waiting}", json={"action": "approve"}).status_code == 403


def test_reviews_need_the_csrf_header(
    client: TestClient, api_owner: Engine, sender: FakeSender
) -> None:
    waiting = unlinked_document(api_owner, "in_review")
    login(client, Role.DATA_STEWARD)
    response = client.post(
        f"{REVIEW}/{waiting}", json={"action": "approve"}, headers={"X-Requested-With": ""}
    )
    assert response.status_code == 403
