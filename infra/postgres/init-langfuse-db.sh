#!/bin/sh
# Runs once, on the first start of an empty Postgres volume.
# Langfuse shares this Postgres container but gets its own database.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE DATABASE langfuse;
SQL
