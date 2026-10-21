-- 001: core journal tables for the intake pipeline.
-- Applied by scripts/migrate.sh with psql vars :app_user / :app_password.

-- Application role for n8n: DML only, no DDL.
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'app_user', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user') \gexec

-- Inbox: every accepted request, exactly once per idempotency_key.
CREATE TABLE inbox (
    id               bigserial PRIMARY KEY,
    idempotency_key  text        NOT NULL UNIQUE,
    source           text        NOT NULL CHECK (source IN ('web', 'telegram', 'email', 'api')),
    payload          jsonb       NOT NULL,
    status           text        NOT NULL DEFAULT 'new'
                                 CHECK (status IN ('new', 'processing', 'done', 'failed')),
    attempt_count    int         NOT NULL DEFAULT 0,
    next_attempt_at  timestamptz NOT NULL DEFAULT now(),
    locked_at        timestamptz,
    last_error       text,
    result           jsonb,
    received_at      timestamptz NOT NULL DEFAULT now(),
    processed_at     timestamptz,
    updated_at       timestamptz NOT NULL DEFAULT now()
);
-- Worker picks by this index: FOR UPDATE SKIP LOCKED over pending rows.
CREATE INDEX inbox_pending_idx ON inbox (next_attempt_at) WHERE status IN ('new', 'failed');
CREATE INDEX inbox_received_idx ON inbox (received_at);

-- Requests rejected by schema validation (never reach inbox), kept for metrics and debugging.
CREATE TABLE intake_rejects (
    id           bigserial PRIMARY KEY,
    received_at  timestamptz NOT NULL DEFAULT now(),
    reasons      jsonb       NOT NULL,
    payload      jsonb
);

-- Failed executions captured by [B24HUB] 99 error handler.
CREATE TABLE errors (
    id              bigserial PRIMARY KEY,
    occurred_at     timestamptz NOT NULL DEFAULT now(),
    workflow_id     text,
    workflow_name   text,
    execution_id    text,
    execution_url   text,
    node_name       text,
    error_message   text,
    error_stack     text,
    context         jsonb
);
CREATE INDEX errors_recent_idx ON errors (workflow_id, occurred_at DESC);

-- One row per HTTP attempt made by [B24HUB] lib b24_call (retries included).
CREATE TABLE b24_call_log (
    id                  bigserial PRIMARY KEY,
    called_at           timestamptz NOT NULL DEFAULT now(),
    method              text        NOT NULL,
    attempt             int         NOT NULL,
    http_status         int,
    ok                  boolean     NOT NULL,
    retryable           boolean     NOT NULL DEFAULT false,
    error_code          text,
    error_description   text,
    duration_ms         int,
    operating           numeric,
    operating_reset_at  timestamptz,
    caller              text,
    execution_id        text
);
CREATE INDEX b24_call_log_time_idx ON b24_call_log (called_at);

-- Active Bitrix24 auth. Stage 1-3: mode=webhook (base_url holds the inbound webhook).
-- Stage 4: mode=oauth (local app tokens). lib b24_call reads this row, so callers never see secrets
-- and workflow exports contain no portal tokens.
CREATE TABLE b24_auth (
    id             smallint    PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    mode           text        NOT NULL CHECK (mode IN ('webhook', 'oauth')),
    base_url       text        NOT NULL,
    access_token   text,
    refresh_token  text,
    expires_at     timestamptz,
    updated_at     timestamptz NOT NULL DEFAULT now()
);

-- Global throttle for Bitrix24 REST: one slot every `interval_ms` shared by all executions.
CREATE TABLE rate_limiter (
    key          text        PRIMARY KEY,
    interval_ms  int         NOT NULL,
    next_slot    timestamptz NOT NULL DEFAULT now()
);
INSERT INTO rate_limiter (key, interval_ms) VALUES ('b24', 500);

-- Reserve the next slot atomically; returns how long the caller must wait before sending.
CREATE FUNCTION rate_limit_acquire(p_key text) RETURNS int
LANGUAGE sql AS $$
    WITH upd AS (
        UPDATE rate_limiter
           SET next_slot = GREATEST(next_slot, clock_timestamp()) + make_interval(secs => interval_ms / 1000.0)
         WHERE key = p_key
     RETURNING next_slot - make_interval(secs => interval_ms / 1000.0) AS my_slot
    )
    SELECT GREATEST(0, ceil(extract(epoch FROM (my_slot - clock_timestamp())) * 1000))::int FROM upd;
$$;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"app_user";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"app_user";
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO :"app_user";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app_user";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO :"app_user";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO :"app_user";
