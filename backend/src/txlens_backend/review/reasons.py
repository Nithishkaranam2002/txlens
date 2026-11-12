"""Item types, actions and the reason codes a reviewer gives for an edit, reject or revert."""

from typing import Literal, get_args

ItemType = Literal["biopsy_document", "note_mention", "condition_mapping", "curated_mapping"]
Action = Literal["approve", "edit", "reject", "revert"]
ReasonCode = Literal[
    "ocr_error",
    "wrong_value",
    "wrong_concept",
    "wrong_negation",
    "wrong_temporality",
    "wrong_experiencer",
    "not_a_finding",
    "insufficient_evidence",
    "other",
]
ITEM_TYPES: tuple[ItemType, ...] = get_args(ItemType)
REASON_CODES: tuple[ReasonCode, ...] = get_args(ReasonCode)
# Which actions each item type allows. A curated mapping row is corrected, never rejected
# (it is one shared vocabulary row); only curated rows can be reverted to an earlier version.
ACTIONS: dict[ItemType, tuple[Action, ...]] = {
    "biopsy_document": ("approve", "edit", "reject"),
    "note_mention": ("approve", "edit", "reject"),
    "condition_mapping": ("approve", "edit", "reject"),
    "curated_mapping": ("approve", "edit", "revert"),
}


class DecisionError(ValueError):
    """A decision that breaks the rules: wrong action for the item, or a missing reason."""


def check_decision(
    item_type: ItemType, action: Action, reason_code: ReasonCode | None, note: str | None
) -> None:
    """Every action but approve needs a reason code; "other" also needs a note."""
    if action not in ACTIONS[item_type]:
        raise DecisionError(f"{action} is not possible for a {item_type.replace('_', ' ')}")
    if action != "approve" and reason_code is None:
        raise DecisionError(f"{action} needs a reason code")
    if reason_code == "other" and not (note or "").strip():
        raise DecisionError("reason 'other' needs a note")
