"""Plain-English labels for the internal codes the review queue would otherwise show.

The raw code still travels with each item (`ReviewItem.code`), so the page can show it on
hover for whoever needs to look it up.
"""

VOCABULARIES = {
    "TXLENS_NOTE_ABBREV": "Note abbreviation",
    "TXLENS_CONDITION": "Condition text",
    "TXLENS_COND_VALUE": "History-of value",
    "TXLENS_DRUG": "Drug name",
    "TXLENS_LAB_NAME": "Lab name",
    "TXLENS_LAB_CODE": "Lab code",
    "TXLENS_PROCEDURE": "Procedure text",
}
# Why a condition text has no concept (pipeline gap reasons and matcher review reasons).
GAP_REASONS = {
    "unmapped": "No matching concept",
    "no_standard_concept": "Matched a concept that is not standard",
    "unsupported_domain": "Matched a concept outside the condition tables",
    "unknown_code": "Unknown SNOMED code",
    "ambiguous": "Several concepts match equally well",
    "qualifier_mismatch": "Best match differs in a qualifier (e.g. acute / chronic)",
    "no_candidate": "No candidate concept found",
    "low_score": "Best match scored too low",
}
# How a note mention was coded.
METHODS = {
    "curated": "Curated abbreviation",
    "vocabulary": "Vocabulary match",
    "ambiguous": "Ambiguous match",
    "llm": "AI fallback",
}


def vocabulary(vocabulary_id: str) -> str:
    """ "TXLENS_NOTE_ABBREV" -> "Note abbreviation" (standard vocabularies keep their name)."""
    return VOCABULARIES.get(vocabulary_id, vocabulary_id)


def gap_reason(reason: str) -> str:
    """ "low_score" -> "Best match scored too low" (an unknown reason as readable text)."""
    return GAP_REASONS.get(reason, reason.replace("_", " "))


def method(name: str) -> str:
    """ "vocabulary" -> "Vocabulary match"."""
    return METHODS.get(name, name.replace("_", " "))


def curated_code(key: str) -> str:
    """A curated row's key as 'Note abbreviation: "abmr"' ('TXLENS_NOTE_ABBREV:abmr')."""
    vocabulary_id, _, code = key.partition(":")
    return f"{vocabulary(vocabulary_id)}: “{code}”"
