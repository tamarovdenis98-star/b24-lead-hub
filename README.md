# b24-lead-hub — Lead Intake & SLA Hub for Bitrix24

> Work in progress. Stages 1–3 of 5 are done: intake, REST wrapper, error handling, processing pipeline,
> SLA smart process, load replay (500 requests → 0 duplicates in CRM, see [docs/results.md](docs/results.md)).
> The full README (problem → solution → measured results) comes in stage 5.

## What is already here
| Piece | What it does |
|---|---|
| `[B24HUB] 01 intake` | HTTPS webhook → header secret → schema validation → `INSERT … ON CONFLICT DO NOTHING` into `inbox` → `200` immediately. Invalid → `422` + row in `intake_rejects`. |
| `[B24HUB] lib b24_call` | The only path to Bitrix24: global ≤2 rps throttle, errors parsed from the body even on HTTP 200, exponential backoff on `QUERY_LIMIT_EXCEEDED` / `OPERATION_TIME_LIMIT`, every attempt journaled with `time.operating`. |
| `[B24HUB] 02 process` | Every 15 s: claims up to 10 inbox rows with `FOR UPDATE SKIP LOCKED`, runs each through `02a`, failed → backoff 1/2/4/8 min, 5 attempts. |
| `[B24HUB] 02a process lead` | Normalize (E.164, e-mail, INN checksum) → per-client lock → DaData (cached) → one lookup batch (`crm.duplicate.findbycomm`, company by INN, own deal by `ORIGIN_ID`) → routing (`route_lead()`: own client → own manager, rules, least loaded) → one create batch: company + requisite + contact + deal + task + SLA item (`crm.item.add`) + timeline comment. Re-runs repair a half-created lead instead of duplicating it. |
| `[B24HUB] 03 sla watcher` | Every minute: overdue SLAs → `im.notify.system.add` to manager and head of sales, SLA item → «SLA нарушен», one Telegram digest. Dry-run switch, ≤10 escalations per run, breaker for mass overdue ([ADR 0008](docs/adr/0008-sla-watcher-safety.md)). |
| `[B24HUB] 04 b24 events` | `ONCRMDEALUPDATE` (push): `auth[application_token]` check (403), re-reads the deal with `crm.deal.get`, closes the SLA via `lib sla_close`. |
| `[B24HUB] 04b deal poller` | Safety net every minute: open SLAs → `crm.deal.list` (`@ID`, `!STAGE_ID: NEW`) → `lib sla_close`. Status by `MOVED_TIME` ([ADR 0009](docs/adr/0009-deal-events-and-poller.md)). |
| `[B24HUB] lib sla_close` | The only place an SLA is closed: met / breached, SLA item stage, journal. |
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
