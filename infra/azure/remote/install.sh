#!/usr/bin/env bash
# Runs on the VM as the admin user, from `make cloud-deploy`, `cloud-update` and `cloud-reset`:
#   bash /opt/txlens/infra/azure/remote/install.sh <domain>
# Writes .env (new secrets, first time only). If demo_snapshot/ holds a snapshot that is not
# the one restored last (or none was restored yet), it restores it with infra/demo/restore.sh
# and cleans it up for the cloud; the snapshot stays on the VM for `make cloud-reset`. Rows
# that cloud-reset saved before the restore (audit trail, guest accounts) are put back.
# Otherwise it rebuilds the images and migrates the schema. Surveillance is pinned to the data
# cut-off (DATA_CUTOFF, the extract date): runs dated after it are removed and the worklist is
# rebuilt as of the cut-off. Then it starts the stack behind Caddy. Never prints a secret.
set -euo pipefail
DOMAIN="$1"
cd "$(dirname "$0")/../../.."
export TXLENS_COMPOSE_OVERRIDE=infra/azure/compose.cloud.yml
# shellcheck source=../../demo/common.sh
source infra/demo/common.sh
SAVED=.reset  # written by reset.sh: rows to keep across a restore

step "Secrets (.env, generated on this server)"
python3 infra/azure/remote/gen_env.py "$PWD" "$DOMAIN"

new_snapshot() {
  [[ -f demo_snapshot/manifest.json ]] \
    && ! cmp -s demo_snapshot/manifest.json .restored-manifest.json
}

# Rows saved by reset.sh go back with their own ids. Through a temporary table, so rows that
# the snapshot already holds are skipped instead of failing the restore.
put_back_saved_rows() {
  [[ -d "$SAVED" ]] || return 0
  step "Putting back the audit trail and guest accounts saved before the reset"
  compose cp "$SAVED/audit.csv" postgres:/tmp/audit.csv
  compose cp "$SAVED/guests.csv" postgres:/tmp/guests.csv
  compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q -v ON_ERROR_STOP=1' <<'SQL'
CREATE TEMP TABLE saved_guests (LIKE app.app_user);
\copy saved_guests FROM '/tmp/guests.csv' WITH (FORMAT csv)
INSERT INTO app.app_user SELECT * FROM saved_guests ON CONFLICT (username) DO NOTHING;
CREATE TEMP TABLE saved_audit (LIKE app.audit_log);
\copy saved_audit FROM '/tmp/audit.csv' WITH (FORMAT csv)
INSERT INTO app.audit_log OVERRIDING SYSTEM VALUE SELECT * FROM saved_audit
  ON CONFLICT (audit_id) DO NOTHING;
SELECT setval(pg_get_serial_sequence('app.audit_log', 'audit_id'), max(audit_id))
  FROM app.audit_log;
SQL
  compose exec -T postgres rm -f /tmp/audit.csv /tmp/guests.csv
  echo "    $(wc -l < "$SAVED/audit.csv" | tr -d ' ') audit rows and" \
    "$(wc -l < "$SAVED/guests.csv" | tr -d ' ') guest accounts kept"
  rm -rf "$SAVED"
}

if new_snapshot; then
  infra/demo/restore.sh
  # Audit rows above this id were written on this server (cloud-reset keeps them).
  sql "SELECT coalesce(max(audit_id), 0) FROM app.audit_log" > .snapshot-audit-max
  put_back_saved_rows

  step "Cloud clean-up: new demo passwords, no sessions or logins from the source machine"
  tools txlens-seed-users
  tools txlens-demo reset-sessions
  sql "DROP DATABASE IF EXISTS langfuse" >/dev/null
  # A test-only login role from the source machine: it has no password here; make sure it
  # cannot log in at all.
  sql "DO \$\$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'txlens_e2e_api') THEN
       ALTER ROLE txlens_e2e_api NOLOGIN; END IF; END \$\$" >/dev/null
  # The containers run as uid 10001 and MLflow opens its SQLite store read-write (Docker
  # Desktop on a Mac maps ownership itself; a Linux host needs the files owned by that uid).
  if [[ "$(uname -s)" == Linux ]]; then
    sudo chown -R 10001:10001 mlruns
    compose restart api worker
  fi
  cp demo_snapshot/manifest.json .restored-manifest.json
  rm -rf mlruns.before-restore-* data/raw.before-restore-*
  echo "    snapshot restored; kept in demo_snapshot/ for make cloud-reset"
  python3 infra/azure/remote/gen_env.py "$PWD" "$DOMAIN"  # now with the data cut-off
else
  step "Building the images; schema up to date"
  compose --profile demo build
  compose up -d --wait postgres
  tools alembic -c backend/alembic.ini upgrade head
fi

step "Guest accounts (guest.clinician, guest.researcher)"
compose up -d --wait postgres
GUESTS=$(sql "SELECT count(*) FROM app.app_user WHERE username LIKE 'guest.%'")
if [[ "$GUESTS" != 2 ]]; then
  tools txlens-seed-users --guests
else
  echo "    already there; open until $(grep -m1 '^GUEST_EXPIRES_AT=' .env | cut -d= -f2)"
fi

CUTOFF=$(grep -m1 '^DATA_CUTOFF=' .env | cut -d= -f2)
[[ -n "$CUTOFF" ]] || fail "DATA_CUTOFF is not set (no data/raw/extract.json?)"
step "Surveillance pinned to the data cut-off ($CUTOFF)"
LATE=$(sql "SELECT count(*) FROM app.surveillance_run WHERE as_of > '$CUTOFF'")
if [[ "$LATE" != 0 ]]; then
  tools txlens-demo drop-surveillance-after "$CUTOFF"
else
  echo "    no surveillance run after the cut-off"
fi

step "Starting the stack behind Caddy (https://$DOMAIN)"
compose --profile demo up -d --wait --remove-orphans
docker image prune -f >/dev/null
if [[ "$LATE" != 0 ]]; then
  step "Rebuilding the worklist as of $CUTOFF"
  api txlens-surveillance run
fi

step "Memory and disk"
free -m 2>/dev/null | sed -n 1,3p || true
df -h / | tail -1
docker stats --no-stream --format '    {{.Name}}: {{.MemUsage}}'
