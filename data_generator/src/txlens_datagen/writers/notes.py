"""Clinical notes as plain-text files, one per note."""

from pathlib import Path

from txlens_datagen.models import Note


def write_note(note: Note, tenant_id: str, raw_dir: Path) -> str:
    """Write raw/notes/<center>/<note_id>.txt and return its path relative to raw/."""
    relative = Path("notes") / tenant_id / f"{note.note_id}.txt"
    path = raw_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(note.text, encoding="utf-8")
    return relative.as_posix()
