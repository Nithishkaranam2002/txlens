"""Seed a throwaway database for the Playwright end-to-end tests and serve the API on it.

Same synthetic data as the API tests (no Spark needed): the generated patients, demo users
with test-only passwords, a surveillance run as of 2025-06-30 and one condition read from
notes. Writes `frontend/e2e/.seed.json` with the ids and names the browser tests look for.
The database is dropped and rebuilt on every run, so tests always start from the same state.

It also puts three pathology reports in review (with a scanned report file, so page images
and crops work) and one AI-curated mapping row in the review queue, and loads the made-up
literature corpus. The copilot answers with a keyword-scripted model and a fake embedder:
no real model is ever called in e2e.

`uv run python backend/tests/e2e_seed.py --serve 8001` (the Playwright config does this) then
serves the API on that database. No Temporal server runs in e2e: reviews are applied by the
API itself, as when no workflow is waiting. The owner URL comes from the usual settings (.env or the
environment); JWT_SECRET and DEID_HMAC_KEY are test-only values set by the Playwright config.
"""

import argparse
import json
import tempfile
from datetime import date
from pathlib import Path

import psycopg
import uvicorn
from api_dataset import build, load
from copilot_testutils import KeywordModel
from db_testutils import migrate
from document_testutils import report_lines_for, seed_document, write_report_pdf
from literature_testutils import CORPUS, FakeEmbedder
from note_testutils import NoteCondition, seed_note_condition
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, pool, text
from sqlalchemy.engine import make_url

from txlens_backend.api.app import create_app
from txlens_backend.api.deps import Role
from txlens_backend.config import Settings, get_settings, require_secret
from txlens_backend.copilot import literature, service
from txlens_backend.db_setup import center_tenants, ensure_login_role, ensure_tenants, libpq_url
from txlens_backend.deid import pseudonym
from txlens_backend.documents.budget import Budget
from txlens_backend.seed_users import seed_users
from txlens_backend.surveillance.run import run_tenant, transplant_configs

DATABASE = "txlens_e2e"
LOGIN = ("txlens_e2e_api", "e2e-only-password")
AS_OF = date(2025, 6, 30)
# Test-only passwords for the throwaway database (never the demo passwords from .env).
PASSWORDS = {
    Role.CLINICIAN: "E2e-clinician-pw-1",
    Role.RESEARCHER: "E2e-researcher-pw-1",
    Role.DATA_STEWARD: "E2e-steward-pw-1",
    Role.ADMIN: "E2e-admin-pw-1",
}
OUT = Path(__file__).parents[2] / "frontend" / "e2e" / ".seed.json"
DOCUMENTS = Path(tempfile.gettempdir()) / "txlens_e2e_documents"
REPORT = "pathology/e2e-report.pdf"
CURATED = ("TXLENS_NOTE_ABBREV", "e2e-abbrev")


class NoWorkflowSender:
    """No document workflow waits in e2e: the API applies each review itself."""

    async def send(self, workflow_id: str, decision: object) -> bool:
        return False


def seed_review(owner: Engine) -> dict[str, object]:
    """Three center_a reports in review (approve, edit, steward view) and a curated row."""
    (DOCUMENTS / "pathology").mkdir(parents=True, exist_ok=True)
    write_report_pdf(DOCUMENTS / REPORT)
    with owner.begin() as conn:
        patients = conn.execute(text(
            "SELECT person_id, mrn, given_name, family_name, birth_date FROM app.patient_identity "
            "WHERE tenant_id = 'center_a' AND given_name IS NOT NULL ORDER BY person_id LIMIT 3"
        )).all()  # fmt: skip
        documents = {}
        for role, p in zip(("approve", "edit", "steward"), patients, strict=True):
            lines = report_lines_for(p.mrn, p.given_name, p.family_name or "Doe", p.birth_date,
                                     date(2025, 5, 6))  # fmt: skip
            doc = seed_document(conn, "center_a", lines, "in_review", p.person_id,
                                day=date(2025, 5, 6), body_top=300)  # fmt: skip
            conn.execute(text("UPDATE app.document SET source_path = :s WHERE document_id = :d"),
                         {"s": REPORT, "d": doc.document_id})  # fmt: skip
            documents[role] = {"id": doc.document_id, "givenName": p.given_name}
        concept = conn.execute(text(
            "SELECT concept_id FROM vocab.concept WHERE standard_concept = 'S' "
            "AND domain_id = 'Condition' ORDER BY concept_id LIMIT 1")).scalar_one()  # fmt: skip
        conn.execute(text(
            "INSERT INTO vocab.source_to_concept_map (source_code, source_concept_id, "
            "source_vocabulary_id, source_code_description, target_concept_id, "
            "target_vocabulary_id, valid_start_date, valid_end_date) VALUES (:c, 0, :v, "
            "'E2E term [curated by AI assistant, pending clinical review]', :t, 'SNOMED', "
            "'2026-11-11', '2099-12-31')"),
            {"v": CURATED[0], "c": CURATED[1], "t": concept})  # fmt: skip
    return {"documents": documents, "curatedKey": ":".join(CURATED)}


def recreate_database(owner_url: str) -> str:
    """Drop, create and migrate the e2e database; returns its owner URL."""
    with psycopg.connect(libpq_url(owner_url), autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{DATABASE}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{DATABASE}"')
    url = make_url(owner_url).set(database=DATABASE).render_as_string(hide_password=False)
    migrate(url)  # creates the txlens_app group role on a fresh server
    with psycopg.connect(libpq_url(url), autocommit=True) as conn:
        ensure_login_role(conn, *LOGIN)
        ensure_tenants(conn, center_tenants())
    return url


def facts(
    owner: Engine, deid_key: str, noted: NoteCondition, review: dict[str, object]
) -> dict[str, object]:
    """Ids and names the browser tests need."""
    with owner.connect() as conn:
        single = conn.execute(text(
            "SELECT f.person_id, i.given_name, i.family_name FROM app.surveillance_flag f "
            "JOIN app.patient_identity i USING (tenant_id, person_id) "
            "WHERE f.tenant_id = 'center_a' AND f.status <> 'resolved' AND f.cleared_at IS NULL "
            "GROUP BY f.person_id, i.given_name, i.family_name "
            "HAVING count(*) = 1 AND bool_and(f.status = 'new') ORDER BY f.person_id LIMIT 1"
        )).one()  # fmt: skip
        other = conn.execute(text(
            "SELECT min(person_id) FROM cdm.person WHERE tenant_id = 'center_b'"
        )).scalar_one()  # fmt: skip
        identities = conn.execute(
            text("SELECT mrn, given_name, family_name FROM app.patient_identity")
        ).all()
    return {
        "passwords": {role.value: password for role, password in PASSWORDS.items()},
        "singleFlagPatient": {
            "id": single.person_id, "name": f"{single.family_name}, {single.given_name}",
        },
        "otherCenterPatient": {
            "id": other, "pseudonym": pseudonym(deid_key, "center_b", other),
        },
        "noteConditionPatient": {
            "id": noted.person_id, "pseudonym": pseudonym(deid_key, "center_a", noted.person_id),
            "givenName": noted.given_name, "condition": noted.concept_name,
        },
        "review": review,
        "names": sorted({n for row in identities for n in (row.given_name, row.family_name) if n}),
        "mrns": sorted(row.mrn for row in identities if row.mrn),
    }  # fmt: skip


def seed(settings: Settings) -> str:
    """Build the e2e database and write the seed file; returns the database's owner URL."""
    url = recreate_database(settings.database_url.get_secret_value())
    owner = create_engine(url, poolclass=pool.NullPool)
    with tempfile.TemporaryDirectory() as folder, owner.begin() as conn:
        load(conn, build(Path(folder)))
    app = create_engine(
        make_url(url).set(username=LOGIN[0], password=LOGIN[1]), poolclass=pool.NullPool
    )
    seed_users(app, list(center_tenants()), PASSWORDS)
    for tenant in center_tenants():
        run_tenant(app, tenant, AS_OF, transplant_configs())
    noted = seed_note_condition(owner, app)
    review = seed_review(owner)
    corpus_budget = Budget(owner, "e2e-literature", 1.0)
    corpus_budget.open()
    literature.index(owner, CORPUS, FakeEmbedder(), corpus_budget)
    deid_key = require_secret("deid_hmac_key", settings.deid_hmac_key)
    OUT.write_text(json.dumps(facts(owner, deid_key, noted, review)))
    print(f"E2E database {DATABASE} ready; seed written to {OUT}")
    return url


def main() -> None:
    """Seed; with --serve PORT, then run the API against the e2e database."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", type=int, metavar="PORT", help="serve the API on this port")
    args = parser.parse_args()
    settings = get_settings()
    url = seed(settings)
    if args.serve:
        app_url = make_url(url).set(username=LOGIN[0], password=LOGIN[1])
        e2e = settings.model_copy(update={
            "database_url": SecretStr(url),
            "app_database_url": SecretStr(app_url.render_as_string(hide_password=False)),
            "documents_root": DOCUMENTS,
        })  # fmt: skip
        api = create_app(e2e)
        api.state.review_sender = NoWorkflowSender()
        api.state.copilot_deps = service.CopilotDeps(e2e, api.state.ctx.engine, FakeEmbedder(),
                                                     KeywordModel())  # fmt: skip
        uvicorn.run(api, host="127.0.0.1", port=args.serve, proxy_headers=False)


if __name__ == "__main__":
    main()
