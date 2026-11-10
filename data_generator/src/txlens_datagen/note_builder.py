"""Builds note text piece by piece and records the exact character span of every fact."""

import re
from dataclasses import dataclass, field
from datetime import date

from txlens_datagen.models import Experiencer, Note, NoteFact, Temporality

PLACEHOLDER = re.compile(r"\{(\w+)\}")


@dataclass(frozen=True)
class Mention:
    """A fact to insert into a template: the words written and what they mean."""

    surface: str
    concept: str
    category: str
    negated: bool = False
    temporality: Temporality = "current"
    experiencer: Experiencer = "patient"
    value: float | None = None
    unit: str | None = None


@dataclass
class NoteBuilder:
    """Appends text; `{name}` placeholders in templates become tracked facts."""

    note_id: str
    parts: list[str] = field(default_factory=list)
    facts: list[NoteFact] = field(default_factory=list)
    length: int = 0

    def write(self, text: str) -> None:
        """Append plain text."""
        self.parts.append(text)
        self.length += len(text)

    def add(self, template: str, **mentions: Mention) -> None:
        """Append a template, turning each `{name}` into the matching mention."""
        pos = 0
        for match in PLACEHOLDER.finditer(template):
            self.write(template[pos : match.start()])
            self.mention(mentions[match.group(1)])
            pos = match.end()
        self.write(template[pos:])

    def line(self, template: str = "", **mentions: Mention) -> None:
        """Append a template followed by a newline."""
        self.add(template, **mentions)
        self.write("\n")

    def mention(self, m: Mention) -> None:
        """Append one mention and record it as a fact."""
        start = self.length
        self.write(m.surface)
        self.facts.append(
            NoteFact(
                note_id=self.note_id,
                concept=m.concept,
                category=m.category,
                text=m.surface,
                start=start,
                end=self.length,
                negated=m.negated,
                temporality=m.temporality,
                experiencer=m.experiencer,
                value=m.value,
                unit=m.unit,
            )
        )

    def build(self, patient_id: str, encounter_id: str, note_date: date, note_type: str) -> Note:
        """Finish the note."""
        return Note(
            note_id=self.note_id,
            patient_id=patient_id,
            encounter_id=encounter_id,
            note_date=note_date,
            note_type=note_type,
            text="".join(self.parts),
            facts=tuple(self.facts),
        )
