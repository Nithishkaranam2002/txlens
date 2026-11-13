# shellcheck shell=bash
# Shared helpers of the demo scripts (sourced, not run). Values from .env are only ever passed
# to containers through `env_file`; these scripts never read or print a secret.

PROJECT="${TXLENS_PROJECT:-txlens}"
WEB_PORT="${DEMO_WEB_PORT:-3000}"
API_PORT="${DEMO_API_PORT:-8000}"
TEMPORAL_UI="${TEMPORAL_UI_PORT:-8233}"
export DEMO_WEB_PORT="$WEB_PORT" DEMO_API_PORT="$API_PORT"

step() { printf '\n==> %s\n' "$*"; }
warn() { printf '    warning: %s\n' "$*" >&2; }
fail() { printf '\nmake demo: %s\n' "$*" >&2; exit 1; }

# TXLENS_COMPOSE_OVERRIDE adds a second compose file (the cloud VM uses infra/azure/compose.cloud.yml).
compose() {
  docker compose -p "$PROJECT" -f infra/docker-compose.yml \
    ${TXLENS_COMPOSE_OVERRIDE:+-f "$TXLENS_COMPOSE_OVERRIDE"} --env-file .env "$@"
}

# A one-off command in the backend image (data, vocabulary subset and MLflow store mounted).
tools() { compose --profile tools run --rm -T tools container-env "$@"; }

# A command inside the running API container (it shares /data/raw with the worker).
api() { compose exec -T api container-env "$@"; }

# One value from the database, as the owner (credentials stay inside the container).
sql() {
  compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "$0"' "$1"
}

# A fingerprint of a folder (relative path and size of every file): equal fingerprints mean
# the folder already holds what a snapshot would put there.
fingerprint() {
  [[ -d "$1" ]] || { echo none; return; }
  # GNU find first: on Linux, BSD-style `stat -f` means "file system" and would not fail.
  (cd "$1" && { find . -type f -printf '%p %s\n' 2>/dev/null \
    || find . -type f -exec stat -f '%N %z' {} +; }) | LC_ALL=C sort | shasum -a 256 | cut -d' ' -f1
}

port_free() { ! lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

preflight() {
  command -v docker >/dev/null || fail "Docker is needed (Docker Desktop on macOS/Windows)."
  [[ -f .env ]] || fail ".env is missing: cp .env.example .env, then fill in the secrets."
  if [[ -z "$(compose ps -q web 2>/dev/null)" ]]; then
    for port in "$WEB_PORT" "$API_PORT"; do
      port_free "$port" || fail "port $port is in use (stop \`make api\` / \`make frontend\`, or set
       DEMO_WEB_PORT / DEMO_API_PORT)."
    done
  fi
}

summary() {
  tools txlens-demo status --web "http://localhost:$WEB_PORT" \
    --api "http://localhost:$API_PORT" --temporal "http://localhost:$TEMPORAL_UI"
}
