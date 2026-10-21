# ADR 0002: Bitrix24 auth is read from Postgres by the REST wrapper only

**Status:** accepted (stage 1)

## Context
A Bitrix24 inbound webhook is a URL with the token in the path (`/rest/<user>/<token>/`).
n8n credentials cannot inject a URL path into an HTTP Request node, and a URL typed into a node
ends up in every workflow export, which goes to a public repository.
Stage 4 switches to OAuth tokens of a local app, which must be stored and refreshed in Postgres anyway.

## Decision
Table `b24_auth` (single row) holds `mode = webhook | oauth`, `base_url` and tokens.
It is seeded from the server `.env` by `scripts/seed_config.sh`.
Only `[B24HUB] lib b24_call` reads it; callers pass `{method, params}` and never see credentials.

## Consequences
- Workflow exports contain no portal URL or token (checked by `scripts/check_exports.py` and gitleaks in CI).
- Switching from webhook to OAuth (stage 4) is a data change, not a change in any caller.
- Successful executions of the lib are **not persisted** (`saveDataSuccessExecution: none`), because
  they would carry `base_url` in execution data. Every HTTP attempt is journaled in `b24_call_log` instead.
- The Postgres role used by n8n (`b24hub_app`) can read the token. Postgres is not exposed outside the
  host: it is bound to 127.0.0.1 and reachable by n8n only over the internal docker network.
