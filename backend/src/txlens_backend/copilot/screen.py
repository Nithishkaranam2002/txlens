"""Deterministic screening of a question before any model sees it.

Clear-cut requests the copilot must refuse are caught by patterns, so no model can be talked
out of refusing them: patient identifiers, other centers, writes, advice about a specific
patient, and instructions aimed at the copilot itself. Subtler cases go on to the model's
classifier and planner, which can refuse too; the SQL guard and the database role still stand
behind both.
"""

import re

from txlens_backend.copilot.actions import RefusalReason

PATTERNS: dict[RefusalReason, list[str]] = {
    "identifiers": [
        r"\bmrns?\b", r"medical record (number|no)", r"\bssn\b", r"social security",
        r"\b(date of birth|birth ?dates?|dob)\b",
        r"\bnames? of (the |these |those |our )?(patients|recipients|people|donors)\b",
        r"\bpatients?'?s? names?\b", r"\bwho (is|are) the patients?\b",
        r"\b(home|street|mailing|postal) address", r"\baddresses of\b",
        r"\bphone numbers?\b", r"\be-?mail addresses?\b",
        r"\b(list|show|give|export|print|dump)\b.{0,40}\b(each|every|individual|all) "
        r"(the )?patients?\b",
        r"\bpatient[- ]level\b", r"\bperson_id\b", r"\bpseudonyms?\b", r"\bR-[0-9a-f]{8,}",
        r"\bpatient_identity\b", r"\bnote_phi_span\b", r"\bre-?identif",
    ],
    "cross_tenant": [
        r"\bcenter_[a-z]\b", r"\bcenter [a-c]\b(?! (lot|few|number|patient|case))",
        r"\b(other|another|all|every|each) (transplant )?(centers?|centres?|hospitals?|"
        r"tenants?)\b", r"\btenant", r"\bacross (centers|centres|hospitals)\b",
    ],
    "write": [
        r"\b(drop|delete|truncate|insert|update|alter|grant|revoke)\b\s+"
        r"(table|from|into|the|all|on|role|\w+\.\w+)",
        r"\b(modify|change|edit|overwrite|correct|remove|erase)\b.{0,30}\b(record|row|data|"
        r"result|value|lab|diagnosis|database|table)s?\b",
        r"\bset_config\b", r"\bset role\b",
    ],
    # Advice about one specific patient (general questions go to the literature instead).
    "clinical_advice": [
        r"\b(this|my|our|that) patient\b(?![s-])",
        r"\b(should|shall|must) (i|we)\b.{0,80}\b(him|her)\b",
        r"\b(my|our) (recipient|transplant patient)\b(?!s)",
        r"\bpatient (#|no\.?|number|id)\s*\w*\d",
    ],
    "prompt_injection": [
        r"\bignore (all |any |the )?(previous|prior|above|earlier) (instructions|rules)",
        r"\b(system|developer) prompt\b", r"\b(admin|developer|god|jailbreak|dan) mode\b",
        r"\byou are now\b", r"\bdisregard (your|the) (rules|guard|instructions)",
        r"\bbypass (the )?(guard|filter|suppression|rls)\b",
    ],
}  # fmt: skip
COMPILED = {reason: [re.compile(p, re.IGNORECASE) for p in patterns]
            for reason, patterns in PATTERNS.items()}  # fmt: skip

REFUSALS: dict[RefusalReason, str] = {
    "identifiers": "I can't share patient identifiers or patient-level records. I only work with "
    "aggregate, de-identified data; I can count patients or summarise a cohort instead.",
    "cross_tenant": "I can only use your own center's data; other centers' data is not "
    "available to you or to me.",
    "write": "I can only read data. I can't change, delete or add records.",
    "clinical_advice": "I can't give advice about a specific patient. Treatment decisions belong "
    "to the care team and the center's protocols; I can summarise cohort data or the "
    "literature instead.",
    "prompt_injection": "I can't change how I work or ignore my safety rules.",
    "out_of_scope": "I can answer questions about your center's transplant data (as "
    "aggregates) and the transplant literature; this question is outside that.",
}  # fmt: skip


def screen(question: str) -> RefusalReason | None:
    """The reason to refuse this question outright, or None to go on."""
    for reason, patterns in COMPILED.items():
        if any(p.search(question) for p in patterns):
            return reason
    return None
