#!/usr/bin/env bash
# Stores the Bitrix24 inbound webhook from .env into Postgres (b24_auth), so that no workflow
# export ever contains the portal token. Run on the host where the stack is up:
#   ./scripts/seed_b24_auth.sh
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
: "${B24_WEBHOOK:?B24_WEBHOOK is not set in .env}"
case "$B24_WEBHOOK" in */) ;; *) B24_WEBHOOK="$B24_WEBHOOK/";; esac
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -v url="$B24_WEBHOOK" <<'SQL'
INSERT INTO b24_auth (id, mode, base_url) VALUES (1, 'webhook', :'url')
ON CONFLICT (id) DO UPDATE SET mode = 'webhook', base_url = EXCLUDED.base_url, updated_at = now();
SQL
echo "b24_auth seeded (mode=webhook)"
