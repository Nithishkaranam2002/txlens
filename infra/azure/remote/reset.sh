#!/usr/bin/env bash
# `make cloud-reset`, on the VM: bring the demo data back to the snapshot (after visitors
# acknowledged, resolved or approved things), keeping the users and the audit trail.
#   bash /opt/txlens/infra/azure/remote/reset.sh <domain>
# Saves the audit rows written on this server and the guest accounts, then makes install.sh
# restore the snapshot again; install.sh puts the saved rows back with their ids. The guests'
# copilot conversations and saved cohorts are not kept: the next visitors start clean. Demo
# and guest passwords stay as they are in .env. Never prints a secret.
set -euo pipefail
DOMAIN="$1"
cd "$(dirname "$0")/../../.."
export TXLENS_COMPOSE_OVERRIDE=infra/azure/compose.cloud.yml
# shellcheck source=../../demo/common.sh
source infra/demo/common.sh
SAVED=.reset

[[ -f demo_snapshot/manifest.json ]] || fail "no snapshot on the VM (make cloud-reset uploads it)"
# Rows saved by a reset that stopped part-way are kept: the database may be half restored now.
if [[ -f "$SAVED/audit.csv" && -f "$SAVED/guests.csv" ]]; then
  step "Using the rows saved by the last, unfinished reset"
else
  SINCE=$(cat .snapshot-audit-max 2>/dev/null || echo 0)
  step "Saving the audit trail (rows after id $SINCE) and the guest accounts"
  mkdir -p "$SAVED"
  out() {  # out <query> <file>: the query's rows as CSV, as the owner
    compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q -v ON_ERROR_STOP=1 \
      -c "\copy ($0) TO STDOUT WITH (FORMAT csv)"' "$1" > "$2.part"
    mv "$2.part" "$2"
  }
  out "SELECT * FROM app.audit_log WHERE audit_id > $SINCE" "$SAVED/audit.csv"
  out "SELECT * FROM app.app_user WHERE username LIKE 'guest.%'" "$SAVED/guests.csv"
fi
echo "    $(wc -l < "$SAVED/audit.csv" | tr -d ' ') audit rows, $(wc -l < "$SAVED/guests.csv" | tr -d ' ') guest accounts"

# No app connections during the restore (the site is down for it anyway): pg_restore --clean
# then has the database and the VM's memory to itself.
step "Stopping the app for the restore"
compose stop caddy web worker api
rm -f .restored-manifest.json  # makes install.sh restore the snapshot again
bash infra/azure/remote/install.sh "$DOMAIN"

step "Clearing the guests' copilot conversations and saved cohorts"
tools txlens-demo clear-guest-work
