#!/usr/bin/env bash
# Runs on the VM for the smoke test (infra/azure/smoke.sh): prints "name value" lines about
# things only visible from inside. Names and counts only, never a secret.
set -euo pipefail
cd "$(dirname "$0")/../../.."
export TXLENS_COMPOSE_OVERRIDE=infra/azure/compose.cloud.yml
# shellcheck source=../../demo/common.sh
source infra/demo/common.sh

# An OpenAI key line with any value in .env, or a non-empty key inside a backend container.
echo "env_file_openai_key $(grep -c '^OPENAI_API_KEY=.' .env || true)"
for service in api worker; do
  set_in_container=$(compose exec -T "$service" sh -c '[ -n "${OPENAI_API_KEY:-}" ] && echo 1 || echo 0')
  echo "${service}_openai_key $set_in_container"
done
echo "langfuse_db $(sql "SELECT count(*) FROM pg_database WHERE datname = 'langfuse'")"
echo "login_attempts $(sql "SELECT count(*) FROM app.login_attempt")"
echo "refresh_tokens $(sql "SELECT count(*) FROM app.refresh_token")"
CUTOFF=$(grep -m1 '^DATA_CUTOFF=' .env | cut -d= -f2)
echo "data_cutoff_set $([[ -n "$CUTOFF" ]] && echo 1 || echo 0)"
echo "worker_data_cutoff_set $(compose exec -T worker sh -c '[ -n "${DATA_CUTOFF:-}" ] && echo 1 || echo 0')"
echo "runs_after_cutoff $(sql "SELECT count(*) FROM app.surveillance_run WHERE as_of > '${CUTOFF:-9999-12-31}'")"
echo "latest_run_is_cutoff $(sql "SELECT (max(as_of) = '${CUTOFF:-0001-01-01}')::int FROM app.surveillance_run")"
echo "guest_accounts $(sql "SELECT count(*) FROM app.app_user WHERE username LIKE 'guest.%' AND is_active")"
GUEST_UNTIL=$(grep -m1 '^GUEST_EXPIRES_AT=' .env | cut -d= -f2)
echo "guest_expiry_matches_env $(sql "SELECT (count(*) = 2)::int FROM app.app_user
  WHERE username LIKE 'guest.%' AND expires_at = '${GUEST_UNTIL:-1970-01-01}'::timestamptz")"
# The copilot's suggested questions, asked offline (cached answers only) as researcher.center_a.
CACHED=$(tools txlens-demo copilot-cache --offline 2>/dev/null | grep -cE '^ +(answered|refused) ' || true)
echo "copilot_suggestions_cached $CACHED"
echo "reboot_required $([[ -f /var/run/reboot-required ]] && echo 1 || echo 0)"
