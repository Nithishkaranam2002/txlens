"""AI-curated mapping rows: their review history and current state.

The history is append-only. Approve and correct each add a version on top of the AI
assistant's concept. A revert undoes the latest version that is still in force and restores
the concept before it. A row is "clinician-reviewed" while at least one approve or correct
is in force; reverting the last one makes it "AI-curated, pending" again.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import Connection, text

PENDING_LABEL = "AI-curated, pending"
REVIEWED_LABEL = "clinician-reviewed"
HistoryAction = Literal["approve", "correct", "revert"]

ALL_HISTORY_SQL = text(
    "SELECT source_vocabulary_id, source_code, action, old_concept_id, new_concept_id, "
    "history_id FROM app.curated_mapping_history ORDER BY history_id"
)
INSERT_SQL = text(
    """
    INSERT INTO app.curated_mapping_history (source_vocabulary_id, source_code, action,
        old_concept_id, new_concept_id, reviewer_id, reviewer_tenant, reason_code, note)
    VALUES (:v, :c, :action, :old, :new, :reviewer,
            NULLIF(current_setting('app.tenant_id', true), ''), :reason, :note)
    RETURNING history_id
    """
)


@dataclass(frozen=True)
class Version:
    """One history entry, as far as the state needs it."""

    action: str
    old_concept_id: int
    new_concept_id: int


@dataclass(frozen=True)
class State:
    """Where a curated row stands after its history."""

    concept_id: int
    reviewed: bool
    in_force: tuple[int, ...]  # indexes of approve/correct entries not reverted, oldest first

    @property
    def label(self) -> str:
        return REVIEWED_LABEL if self.reviewed else PENDING_LABEL


def state(original_concept_id: int, entries: Sequence[Version]) -> State:
    """Replay the history on top of the AI assistant's concept."""
    stack: list[int] = []
    for index, entry in enumerate(entries):
        if entry.action == "revert":
            if stack:
                stack.pop()
        else:
            stack.append(index)
    concept = entries[stack[-1]].new_concept_id if stack else original_concept_id
    return State(concept, bool(stack), tuple(stack))


def histories(conn: Connection) -> dict[tuple[str, str], list[Version]]:
    """Every curated row's history, by (source vocabulary, source code)."""
    found: dict[tuple[str, str], list[Version]] = {}
    for row in conn.execute(ALL_HISTORY_SQL):
        version = Version(row.action, row.old_concept_id, row.new_concept_id)
        found.setdefault((row.source_vocabulary_id, row.source_code), []).append(version)
    return found


def add_entry(
    conn: Connection, vocabulary: str, code: str, action: HistoryAction, old: int, new: int,
    reviewer_id: str, reason_code: str | None, note: str | None,
) -> int:  # fmt: skip
    """Append one version; the reviewer's center is the transaction's tenant."""
    return int(
        conn.execute(
            INSERT_SQL,
            {"v": vocabulary, "c": code, "action": action, "old": old, "new": new,
             "reviewer": reviewer_id, "reason": reason_code, "note": note},
        ).scalar_one()
    )  # fmt: skip
