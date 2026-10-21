# Runbook

> Stage 2 draft. Incident procedures ("rollback in 5 minutes") are completed in stage 5.

## Deploy / update
```bash
# on the VPS
cd /opt/b24-lead-hub && git pull --ff-only
docker compose -f docker-compose.yml -f docker-compose.vps.yml run --rm migrate
./scripts/seed_config.sh
# from a machine with N8N_URL / N8N_API_KEY
python scripts/n8n_sync.py deploy          # workflows/*.json -> n8n (by name, tagged, activated)
./scripts/export_workflows.sh              # n8n -> workflows/*.json + secret check, then commit
```

## Portal setup (once per portal)
```bash
python scripts/b24_setup.py > sql/seed/portal_settings.sql   # smart process "Контроль SLA", stages
scp sql/seed/portal_settings.sql vps:/opt/b24-lead-hub/sql/seed/ && ssh vps /opt/b24-lead-hub/scripts/seed_config.sh
```

## Where to look
| Question | Query |
|---|---|
| What happened to a lead? | `SELECT step, detail, at FROM lead_events WHERE inbox_id = …  ORDER BY id` |
| What is stuck? | `SELECT id, status, attempt_count, last_error, next_attempt_at FROM inbox WHERE status NOT IN ('done','rejected')` |
| Is Bitrix24 throttling us? | `SELECT error_code, count(*) FROM b24_call_log WHERE called_at > now() - interval '1 hour' GROUP BY 1` |
| Recent failures | `SELECT occurred_at, workflow_name, node_name, error_message, execution_url FROM errors ORDER BY id DESC LIMIT 20` |

## Retry a lead now
```sql
UPDATE inbox SET status = 'failed', next_attempt_at = now() WHERE id = …;   -- picked up within 15 s
-- terminal after 5 attempts: also reset attempt_count = 0
```

## Reset the demo portal
```bash
python scripts/cleanup_demo.py            # dry run: counts of [DEMO] entities created by the hub
python scripts/cleanup_demo.py --yes      # delete them
# journal (on the VPS):
docker exec b24hub-postgres psql -U b24hub_owner -d b24hub -c \
  "TRUNCATE inbox, intake_rejects, assignments, lead_events, b24_call_log, errors, client_locks, dadata_cache RESTART IDENTITY CASCADE"
```
