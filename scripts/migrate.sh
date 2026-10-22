#!/bin/sh
# Applies sql/NNN_*.sql exactly once each, in lexical order, inside a transaction per file.
set -eu
psql -v ON_ERROR_STOP=1 -q -c "CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
for f in $(ls /sql/*.sql | sort); do
  v=$(basename "$f")
  if [ "$(psql -tAc "SELECT 1 FROM schema_migrations WHERE version='$v'")" = "1" ]; then
    echo "skip  $v"; continue
  fi
  echo "apply $v"
  psql -v ON_ERROR_STOP=1 -q --single-transaction \
       -v app_user="$APP_DB_USER" -v app_password="$APP_DB_PASSWORD" \
       -v ro_user="${RO_DB_USER:-b24hub_ro}" -v ro_password="${RO_DB_PASSWORD:-change-me-ro}" \
       -f "$f" -c "INSERT INTO schema_migrations(version) VALUES ('$v')"
done
echo "migrations up to date"
