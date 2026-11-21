#!/usr/bin/env bash
# Rebuild the LOCAL database from scratch: shim -> migrations (in order) -> seeds.
# Refuses to run against anything that isn't localhost.
set -euo pipefail
DB_URL="${DATABASE_URL:-postgresql://postgres:postgres@localhost:5432/lanekeeper}"
case "$DB_URL" in
  *@localhost*|*@127.0.0.1*|*@postgres:*) ;;
  *) echo "refusing: db-reset only runs against a local database (got ${DB_URL%%@*}@...)" >&2; exit 2 ;;
esac
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ADMIN_URL="${DB_URL%/*}/postgres"
DB_NAME="${DB_URL##*/}"
psql -q "$ADMIN_URL" -v ON_ERROR_STOP=1 -c "drop database if exists \"$DB_NAME\" with (force)" -c "create database \"$DB_NAME\""
run() { psql -q "$DB_URL" -v ON_ERROR_STOP=1 -f "$1" >/dev/null; echo "  applied $(basename "$1")"; }
run "$ROOT/supabase/tests/shim.sql"
for f in "$ROOT"/supabase/migrations/*.sql; do run "$f"; done
[ "${SKIP_SEED:-0}" = 1 ] && exit 0
run "$ROOT/supabase/seed.sql"
for f in "$ROOT"/supabase/seed/*.sql; do [ -e "$f" ] && run "$f"; done
echo "local database ready: $DB_NAME"
