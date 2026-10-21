#!/usr/bin/env bash
# Copies environment-specific values from .env into Postgres, so workflow exports stay clean:
#   B24_WEBHOOK -> b24_auth (portal token never appears in n8n workflows)
#   TG_CHAT_ID  -> settings.alert_chat_id
# Run on the host where the stack is up: ./scripts/seed_config.sh
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
: "${B24_WEBHOOK:?B24_WEBHOOK is not set in .env}"
: "${TG_CHAT_ID:?TG_CHAT_ID is not set in .env}"
case "$B24_WEBHOOK" in */) ;; *) B24_WEBHOOK="$B24_WEBHOOK/";; esac
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -v url="$B24_WEBHOOK" -v chat="$TG_CHAT_ID" <<'SQL'
INSERT INTO b24_auth (id, mode, base_url) VALUES (1, 'webhook', :'url')
ON CONFLICT (id) DO UPDATE SET mode = 'webhook', base_url = EXCLUDED.base_url, updated_at = now();
INSERT INTO settings (key, value) VALUES ('alert_chat_id', :'chat')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();
SQL
echo "config seeded: b24_auth(mode=webhook), settings.alert_chat_id"
