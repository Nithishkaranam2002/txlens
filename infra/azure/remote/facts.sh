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
echo "reboot_required $([[ -f /var/run/reboot-required ]] && echo 1 || echo 0)"
