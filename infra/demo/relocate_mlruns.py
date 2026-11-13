"""Point a restored MLflow store at its new folder.

MLflow records absolute paths (artifact locations in `mlflow.db`, paths in MLmodel and
meta.yaml files). A snapshot taken in one checkout and restored in another (e.g. a cloud VM)
still points at the old folder, so the risk model would not load. This rewrites every
occurrence of the old mlruns path to the new one. Standard library only: `restore.sh` runs it
with the host's python3.

Usage: python3 relocate_mlruns.py <mlruns folder> <old mlruns path> <new mlruns path>
"""

import sqlite3
import sys
from pathlib import Path

TEXT_FILES = ("MLmodel", "meta.yaml")


def relocate_database(db: Path, old: str, new: str) -> int:
    """Replace `old` with `new` in every text column of every table. Returns rows changed."""
    changed = 0
    with sqlite3.connect(db) as conn:
        tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )]  # fmt: skip
        for table in tables:
            for column in conn.execute(f'PRAGMA table_info("{table}")').fetchall():
                name, declared = column[1], (column[2] or "").upper()
                if not any(kind in declared for kind in ("CHAR", "TEXT", "CLOB")):
                    continue
                cursor = conn.execute(
                    f'UPDATE "{table}" SET "{name}" = replace("{name}", ?, ?) '
                    f'WHERE instr("{name}", ?) > 0',
                    (old, new, old),
                )
                changed += cursor.rowcount
    return changed


def relocate_files(folder: Path, old: str, new: str) -> int:
    """Replace `old` with `new` in MLflow's MLmodel and meta.yaml files. Returns files changed."""
    changed = 0
    for path in folder.rglob("*"):
        if path.name not in TEXT_FILES or not path.is_file():
            continue
        content = path.read_text()
        if old in content:
            path.write_text(content.replace(old, new))
            changed += 1
    return changed


def relocate(folder: Path, old: str, new: str) -> tuple[int, int]:
    """Rewrite the database and the files; (rows, files) changed. No-op when old == new."""
    if old.rstrip("/") == new.rstrip("/"):
        return 0, 0
    old, new = old.rstrip("/"), new.rstrip("/")
    rows = relocate_database(folder / "mlflow.db", old, new)
    return rows, relocate_files(folder, old, new)


def main(argv: list[str]) -> int:
    """Entry point."""
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    rows, files = relocate(Path(argv[0]), argv[1], argv[2])
    print(f"    MLflow paths moved to {argv[2]}: {rows} database rows, {files} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
