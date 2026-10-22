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

## SLA watcher controls
```sql
UPDATE settings SET value = 'true'  WHERE key = 'sla_watch_dry_run';       -- stop sending, keep counting
UPDATE settings SET value = '10'    WHERE key = 'sla_escalation_limit';    -- escalations per minute
UPDATE settings SET value = '50'    WHERE key = 'sla_escalation_breaker';  -- mass-overdue threshold
SELECT * FROM sla_watch_runs ORDER BY id DESC LIMIT 20;                    -- what it did / would do
```
Before switching dry run off on a portal with history, check `SELECT count(*) FROM assignments WHERE
sla_status = 'open' AND sla_due < now()` — every row is an escalation.

## Check CRM links against the journal
```bash
python scripts/check_crm_links.py            # deal -> contact / company, SLA item -> deal
python scripts/check_crm_links.py --fix      # repair from inbox.result
```

## Reverse proxy requirements (local app)
Bitrix24 fetches the site root cross-origin and shows the app pages in an iframe. n8n sends
`Cross-Origin-Resource-Policy: same-origin` + `X-Frame-Options: SAMEORIGIN` on `/` and
`Content-Security-Policy: sandbox …` (opaque origin, breaks the BX24 SDK and `installFinish`) on webhooks.
The proxy overrides this **only** for the root and `/webhook/b24hub/app*` (Caddy, inside the n8n site block):
```caddyfile
@b24root path /
header @b24root >Cross-Origin-Resource-Policy cross-origin

@b24app path /webhook/b24hub/app /webhook/b24hub/app/*
header @b24app {
	>Content-Security-Policy "frame-ancestors https://*.bitrix24.ru"
	>Cross-Origin-Resource-Policy cross-origin
	-X-Frame-Options
}
```
The editor (`/signin`, `/home/...`) keeps `same-origin` / `SAMEORIGIN`; other webhooks keep n8n's sandbox CSP.

After editing a bind-mounted Caddyfile check that the container sees it
(`docker exec app-caddy-1 grep -c b24hub /etc/caddy/Caddyfile`): an editor that replaces the file
leaves the container on the old inode, and `caddy reload` then silently reloads the old config.
