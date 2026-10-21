#!/usr/bin/env bash
# Copies environment-specific values from .env into Postgres, so workflow exports stay clean:
#   B24_WEBHOOK      -> b24_auth (portal token never appears in n8n workflows)
#   TG_CHAT_ID       -> settings.alert_chat_id
#   B24_ROP_ID       -> settings.rop_user_id (head of sales, gets SLA escalations)
#   B24_MANAGERS     -> managers + demo routing table (sql/seed/routing_demo.sql), if exactly 3 ids
#   CRM_TITLE_PREFIX -> settings.crm_title_prefix (e.g. "[DEMO] " on a demo portal)
# Run on the host where the stack is up: ./scripts/seed_config.sh
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
: "${B24_WEBHOOK:?B24_WEBHOOK is not set in .env}"
: "${TG_CHAT_ID:?TG_CHAT_ID is not set in .env}"
case "$B24_WEBHOOK" in */) ;; *) B24_WEBHOOK="$B24_WEBHOOK/";; esac
PSQL=(docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d "$POSTGRES_DB")

"${PSQL[@]}" -v url="$B24_WEBHOOK" -v chat="$TG_CHAT_ID" -v rop="${B24_ROP_ID:-}" -v prefix="${CRM_TITLE_PREFIX:-}" <<'SQL'
INSERT INTO b24_auth (id, mode, base_url) VALUES (1, 'webhook', :'url')
ON CONFLICT (id) DO UPDATE SET mode = 'webhook', base_url = EXCLUDED.base_url, updated_at = now();
INSERT INTO settings (key, value) VALUES ('alert_chat_id', :'chat'), ('rop_user_id', :'rop'), ('crm_title_prefix', :'prefix')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();
SQL

IFS=',' read -r -a M <<< "${B24_MANAGERS:-}"
if [ "${#M[@]}" -eq 3 ]; then
  "${PSQL[@]}" -v m1="${M[0]}" -v m2="${M[1]}" -v m3="${M[2]}" < sql/seed/routing_demo.sql
  echo "demo routing seeded for managers ${B24_MANAGERS}"
fi
if [ -f sql/seed/portal_settings.sql ]; then
  "${PSQL[@]}" < sql/seed/portal_settings.sql   # output of scripts/b24_setup.py (not in git)
  echo "portal settings applied"
fi
echo "config seeded"
