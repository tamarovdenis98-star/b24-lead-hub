#!/usr/bin/env bash
# Exports all [B24HUB] workflows from n8n into workflows/*.json (credential ids and workflow ids
# replaced by names), then fails if anything secret-looking slipped into the export.
# Env: N8N_URL, N8N_API_KEY (e.g. `set -a; . ./.env; set +a`).
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/n8n_sync.py export 2>/dev/null || python scripts/n8n_sync.py export
python3 scripts/check_exports.py 2>/dev/null || python scripts/check_exports.py
