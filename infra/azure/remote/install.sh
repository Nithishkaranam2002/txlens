#!/usr/bin/env bash
# Runs on the VM as the admin user, from `make cloud-deploy` / `make cloud-update`:
#   bash /opt/txlens/infra/azure/remote/install.sh <domain>
# Writes .env (new secrets, first time only). If a snapshot was uploaded to demo_snapshot/, it
# restores it with infra/demo/restore.sh, cleans it up for the cloud and deletes the upload;
# otherwise it rebuilds the images. Surveillance is pinned to the data cut-off (DATA_CUTOFF,
# the extract date): runs dated after it are removed and the worklist is rebuilt as of the
# cut-off. Then it starts the stack behind Caddy. Never prints a secret.
set -euo pipefail
DOMAIN="$1"
cd "$(dirname "$0")/../../.."
export TXLENS_COMPOSE_OVERRIDE=infra/azure/compose.cloud.yml
# shellcheck source=../../demo/common.sh
source infra/demo/common.sh

step "Secrets (.env, generated on this server)"
python3 infra/azure/remote/gen_env.py "$PWD" "$DOMAIN"

if [[ -f demo_snapshot/manifest.json ]]; then
  infra/demo/restore.sh

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
  rm -rf demo_snapshot mlruns.before-restore-* data/raw.before-restore-*
  echo "    snapshot restored; the uploaded copy is deleted"
  python3 infra/azure/remote/gen_env.py "$PWD" "$DOMAIN"  # now with the data cut-off
else
  step "Building the images"
  compose --profile demo build
fi

CUTOFF=$(grep -m1 '^DATA_CUTOFF=' .env | cut -d= -f2)
[[ -n "$CUTOFF" ]] || fail "DATA_CUTOFF is not set (no data/raw/extract.json?)"
step "Surveillance pinned to the data cut-off ($CUTOFF)"
compose up -d --wait postgres
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
