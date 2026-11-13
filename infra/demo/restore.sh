#!/usr/bin/env bash
# `make demo-restore`: bring back the demo saved by `make demo-snapshot`, offline (no LLM
# calls, no downloads beyond the base images), then start the stack and print URLs and users.
#
# The database is replaced (pg_restore --clean); mlruns/ and data/raw/ come from the snapshot,
# and existing folders are moved aside (never deleted). Checksums and the schema revision are
# checked first; a snapshot from an older schema is migrated forward after the restore. A
# snapshot from another folder or machine works too: MLflow's absolute paths are moved here.
set -euo pipefail
cd "$(dirname "$0")/../.."
source infra/demo/common.sh

SNAP=demo_snapshot
[[ -f "$SNAP/manifest.json" ]] || fail "no snapshot in $SNAP/ (run make demo-snapshot first)."
preflight

step "Checking the snapshot"
python3 - "$SNAP" "$PWD" <<'PY'
import hashlib, json, pathlib, sys
folder, checkout = pathlib.Path(sys.argv[1]), sys.argv[2]
manifest = json.loads((folder / "manifest.json").read_text())
for name, meta in manifest["files"].items():
    digest = hashlib.sha256()
    with (folder / name).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    if digest.hexdigest() != meta["sha256"]:
        sys.exit(f"{name} does not match the manifest (damaged or edited snapshot)")
if manifest["checkout"] != checkout:
    print(f"    note: taken in {manifest['checkout']}; MLflow paths are moved here")
print(f"    snapshot of {manifest['created']}, schema {manifest['alembic_revision']}: files OK")
PY
SNAP_REVISION=$(python3 -c "import json; print(json.load(open('$SNAP/manifest.json'))['alembic_revision'])")
ls backend/migrations/versions/"${SNAP_REVISION}"_*.py >/dev/null 2>&1 \
  || fail "the snapshot's schema ($SNAP_REVISION) is newer than this checkout: update the code."

step "Starting Postgres and Temporal"
compose --profile demo build
compose up -d --wait postgres temporal

step "Restoring roles and the database (a few minutes for the full vocabulary)"
compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d postgres -q' < "$SNAP/roles.sql" \
  2>/dev/null || true  # existing roles (the owner) report "already exists"
# Owners are kept: the copilot views must stay owned by their non-superuser role, or RLS
# would no longer apply through them. A parallel restore needs the dump as a file.
compose cp "$SNAP/txlens.dump" postgres:/tmp/txlens.dump
compose exec -T postgres sh -c \
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists -j 4 /tmp/txlens.dump;
   status=$?; rm -f /tmp/txlens.dump; exit $status' \
  || fail "pg_restore reported errors (see above)"

step "MLflow store and source files"
STAMP=$(date +%Y%m%d-%H%M%S)
for pair in "mlruns:mlruns.tgz" "data/raw:data_raw.tgz"; do
  dir=${pair%%:*} archive=${pair#*:}
  saved=$(python3 -c "import json; print(json.load(open('$SNAP/manifest.json'))['folders']['$dir'])")
  if [[ "$(fingerprint "$dir")" == "$saved" ]]; then
    echo "    $dir already matches the snapshot"
    continue
  fi
  if [[ -e "$dir" ]]; then
    mv "$dir" "$dir.before-restore-$STAMP"
    echo "    kept the old $dir as $dir.before-restore-$STAMP"
  fi
  mkdir -p "$(dirname "$dir")" && tar -xzf "$SNAP/$archive"
done
# MLflow stores absolute paths: a snapshot from another checkout must point here instead.
SNAP_CHECKOUT=$(python3 -c "import json; print(json.load(open('$SNAP/manifest.json'))['checkout'])")
python3 infra/demo/relocate_mlruns.py mlruns "$SNAP_CHECKOUT/mlruns" "$PWD/mlruns"

step "Schema up to date, login role password from .env"
tools alembic -c backend/alembic.ini upgrade head
tools txlens-db-setup

step "Starting the API, worker and frontend"
compose --profile demo up -d --wait api worker web

summary
