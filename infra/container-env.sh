#!/bin/sh
# Entrypoint of the backend image. .env holds host-side database URLs (localhost); inside the
# compose network Postgres is "$TXLENS_DB_HOST:5432", so swap the host part of both URLs, then
# run the command. Values are rewritten in place and never printed.
set -eu

if [ -n "${TXLENS_DB_HOST:-}" ]; then
    swap() { printf '%s' "$1" | sed -E "s#@(localhost|127\.0\.0\.1)(:[0-9]+)?/#@${TXLENS_DB_HOST}:5432/#"; }
    DATABASE_URL=$(swap "${DATABASE_URL:-}")
    APP_DATABASE_URL=$(swap "${APP_DATABASE_URL:-}")
    export DATABASE_URL APP_DATABASE_URL
fi

exec "$@"
