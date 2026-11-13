#!/usr/bin/env bash
# `make demo-snapshot`: save the fully loaded demo into demo_snapshot/ (git-ignored), so
# `make demo-restore` can bring it back offline in minutes, with every paid LLM answer kept.
#
# Saved: the database (custom-format pg_dump: data, vocabulary, LLM cache, embeddings), the
# cluster roles without passwords (db-setup sets the login password again from .env), the
# MLflow store (mlruns/) and the source files the app reads (data/raw: PDFs, notes, labs).
# manifest.json holds the schema revision, row counts and checksums.
#
# The dump contains the licensed OMOP vocabulary: keep it on this machine; never commit or
# share it. MLflow records absolute paths, so a snapshot restores into this checkout only.
set -euo pipefail
cd "$(dirname "$0")/../.."
source infra/demo/common.sh

[[ -f .env ]] || fail ".env is missing."
OUT=demo_snapshot
mkdir -p "$OUT"
compose --profile demo build api
compose up -d --wait postgres

step "Roles (no passwords) and database"
compose exec -T postgres sh -c 'pg_dumpall -U "$POSTGRES_USER" --roles-only --no-role-passwords' \
  | grep -E '^(CREATE|ALTER|GRANT) ROLE |^GRANT ' | grep -v '_fixture_\|_test_' > "$OUT/roles.sql"
compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -Z 6' \
  > "$OUT/txlens.dump"

step "MLflow store and source files"
# No macOS extended attributes (GNU tar on a Linux restore would warn about each file).
COPYFILE_DISABLE=1 tar --no-xattrs -czf "$OUT/mlruns.tgz" mlruns
COPYFILE_DISABLE=1 tar --no-xattrs -czf "$OUT/data_raw.tgz" data/raw

step "Manifest"
REVISION=$(sql "SELECT version_num FROM alembic_version")
COUNTS=$(tools txlens-demo counts)
python3 - "$OUT" "$REVISION" "$COUNTS" "$(git rev-parse HEAD)" "$PWD" \
  "$(fingerprint mlruns)" "$(fingerprint data/raw)" <<'PY'
import datetime, hashlib, json, pathlib, sys
out, revision, counts, commit, checkout, mlruns, raw = sys.argv[1:]
folder = pathlib.Path(out)

def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()

files = {p.name: {"bytes": p.stat().st_size, "sha256": sha256(p)}
         for p in sorted(folder.iterdir()) if p.name != "manifest.json"}
manifest = {"created": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
            "git_commit": commit, "alembic_revision": revision, "checkout": checkout,
            "counts": json.loads(counts), "files": files,
            "folders": {"mlruns": mlruns, "data/raw": raw}}
(folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
total = sum(f["bytes"] for f in files.values()) / 1e9
print(f"    {len(files)} files, {total:.2f} GB, schema {revision}")
PY
printf '\nSnapshot saved in %s/ (local only: it holds the licensed vocabulary).\n' "$OUT"
