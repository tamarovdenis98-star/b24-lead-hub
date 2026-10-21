-- 008: SLA watcher and Bitrix24 outbound events.

-- application_token of the Bitrix24 outbound webhook (ONCRMDEALUPDATE); compared by [B24HUB] 04.
ALTER TABLE b24_auth ADD COLUMN outbound_token text;

-- One row per watcher run that found something (or ran in dry-run mode).
CREATE TABLE sla_watch_runs (
    id          bigserial   PRIMARY KEY,
    at          timestamptz NOT NULL DEFAULT now(),
    mode        text        NOT NULL CHECK (mode IN ('idle', 'dry_run', 'breaker', 'escalate')),
    due_count   int         NOT NULL,   -- overdue, not yet escalated
    planned     int         NOT NULL,   -- would be / will be escalated in this run (<= limit)
    details     jsonb       NOT NULL DEFAULT '{}'
);
CREATE INDEX sla_watch_runs_at_idx ON sla_watch_runs (at);

INSERT INTO settings (key, value) VALUES
    ('sla_watch_dry_run',               'true'),  -- new deployments start safe: count only, send nothing
    ('sla_escalation_limit',            '10'),    -- max escalations per run (per minute)
    ('sla_escalation_breaker',          '50'),    -- more overdue than this = incident, not individual escalations
    ('sla_watch_last_breaker_alert_at', ''),
    ('portal_url',                      '')       -- for links in Telegram, seeded from B24_PORTAL
ON CONFLICT (key) DO NOTHING;

GRANT SELECT, INSERT, UPDATE, DELETE ON sla_watch_runs TO :"app_user";
GRANT USAGE, SELECT ON SEQUENCE sla_watch_runs_id_seq TO :"app_user";
