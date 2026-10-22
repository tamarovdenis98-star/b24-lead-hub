#!/usr/bin/env bash
# Rolls the n8n workflows back (or forward) to the versions stored in a git revision and reports how long it took.
#   ./scripts/rollback.sh <git-ref>            e.g. ./scripts/rollback.sh HEAD~3   or a tag
# Env: N8N_URL, N8N_API_KEY. The database is not touched: migrations are additive (new tables / columns),
# so an older workflow version runs against the newer schema.
set -euo pipefail
cd "$(dirname "$0")/.."
REF="${1:?usage: rollback.sh <git-ref>}"
T0=$(date +%s)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/workflows"
for f in $(git ls-tree --name-only "$REF" workflows/); do
  git show "$REF:$f" > "$TMP/$f"
done
PY=python3; "$PY" -c '' 2>/dev/null || PY=python   # Windows: python3 may be a Store stub
"$PY" scripts/n8n_sync.py deploy "$TMP"/workflows/*.json
echo "deployed workflows from $REF ($(git rev-parse --short "$REF")) in $(( $(date +%s) - T0 )) s"
