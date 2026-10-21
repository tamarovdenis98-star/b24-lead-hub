# b24-lead-hub — Lead Intake & SLA Hub for Bitrix24

> Work in progress. Stage 1 of 5 is done: infrastructure, intake, REST wrapper, error handling.
> The full README (problem → solution → measured results) comes in stage 5.

## What is already here
| Piece | What it does |
|---|---|
| `[B24HUB] 01 intake` | HTTPS webhook → header secret → schema validation → `INSERT … ON CONFLICT DO NOTHING` into `inbox` → `200` immediately. Invalid → `422` + row in `intake_rejects`. |
| `[B24HUB] lib b24_call` | The only path to Bitrix24: global ≤2 rps throttle, errors parsed from the body even on HTTP 200, exponential backoff on `QUERY_LIMIT_EXCEEDED` / `OPERATION_TIME_LIMIT`, every attempt journaled with `time.operating`. |
| `[B24HUB] 99 error handler` | Error Workflow for all of the above: `errors` table + Telegram alert with a link to the execution. |
| `[B24HUB] 90 test harness`, `91 b24 mock` | Test entry point for the lib, and a deterministic Bitrix24 failure simulator (inactive by default). |

Design decisions: [docs/adr](docs/adr). Stage 1 test evidence: [docs/results.md](docs/results.md).

## Quick start
```bash
cp .env.example .env                      # fill in values
docker compose up -d                      # postgres + one-shot migrations
./scripts/seed_config.sh                  # Bitrix24 webhook + alert chat id -> Postgres
set -a; . ./.env; set +a
python scripts/n8n_sync.py credentials    # n8n credentials from env
python scripts/n8n_sync.py deploy         # workflows/*.json: create/update, tag, activate
./scripts/export_workflows.sh             # n8n -> workflows/*.json + secret check
```
