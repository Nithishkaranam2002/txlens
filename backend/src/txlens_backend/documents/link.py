"""Find whose biopsy a report describes, without the LLM.

The MRN and procedure date come from the report header (parsed locally, phi.py). A report is
linked to the biopsy procedure in OMOP of the patient with that MRN that lies closest to the
procedure date, within 7 days. OCR can damage the MRN, so among patients with a biopsy in that
window an MRN that differs by an OCR slip (similarity >= 85) is accepted when it is the only
close one. The linked biopsy also tells the organ, more reliably than the report text.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from rapidfuzz import fuzz
from sqlalchemy import Connection, bindparam, text

from txlens_backend.clinical_codes import BIOPSY
from txlens_backend.documents.phi import Header, Identity
from txlens_backend.documents.schema import Organ
from txlens_backend.documents.validate import DATE_WINDOW_DAYS
from txlens_backend.services.facts import concept_keys

MRN_SIMILARITY = 85
CANDIDATES = text(
    "SELECT i.person_id, i.mrn, i.given_name, i.family_name, i.birth_date, "
    "p.procedure_occurrence_id, p.procedure_date, p.procedure_concept_id "
    "FROM app.patient_identity i "
    "JOIN cdm.procedure_occurrence p ON p.person_id = i.person_id "
    "WHERE p.procedure_concept_id IN :biopsies AND p.procedure_date BETWEEN :start AND :end"
).bindparams(bindparam("biopsies", expanding=True))


@dataclass(frozen=True)
class Link:
    """The biopsy procedure a report belongs to, and that patient's identifiers."""

    person_id: int
    procedure_occurrence_id: int
    procedure_date: date
    organ: Organ
    identity: Identity


def link_report(conn: Connection, header: Header) -> Link | None:
    """The biopsy this report describes, or None if it cannot be found safely."""
    if header.mrn is None or header.procedure_date is None:
        return None
    organs = concept_keys(conn, BIOPSY)
    if not organs:
        return None
    window = timedelta(days=DATE_WINDOW_DAYS)
    rows = conn.execute(
        CANDIDATES,
        {
            "biopsies": list(organs),
            "start": header.procedure_date - window,
            "end": header.procedure_date + window,
        },
    ).all()
    wanted = header.mrn.upper()
    exact = [r for r in rows if r.mrn.upper() == wanted]
    close = [r for r in rows if fuzz.ratio(r.mrn.upper(), wanted) >= MRN_SIMILARITY]
    matches = exact or (close if len({r.person_id for r in close}) == 1 else [])
    if not matches:
        return None
    best = min(matches, key=lambda r: abs((r.procedure_date - header.procedure_date).days))
    identity = Identity(best.mrn, best.given_name, best.family_name, best.birth_date)
    organ: Organ = "kidney" if organs[best.procedure_concept_id] == "kidney" else "heart"
    return Link(best.person_id, best.procedure_occurrence_id, best.procedure_date, organ, identity)
