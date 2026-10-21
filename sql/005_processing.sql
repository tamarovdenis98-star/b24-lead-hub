-- 005: lead processing — routing, assignments with SLA, per-lead event journal, DaData cache.

-- 'rejected' = data problem (no usable contact channel), never retried. 'failed' = retried while
-- attempt_count < settings.process_max_attempts, then terminal.
ALTER TABLE inbox DROP CONSTRAINT inbox_status_check;
ALTER TABLE inbox ADD CONSTRAINT inbox_status_check
    CHECK (status IN ('new', 'processing', 'done', 'failed', 'rejected'));
ALTER TABLE inbox ADD COLUMN execution_id text;  -- n8n execution of the last processing attempt
DROP INDEX inbox_pending_idx;
CREATE INDEX inbox_pending_idx ON inbox (next_attempt_at) WHERE status IN ('new', 'failed', 'processing');

CREATE TABLE managers (
    user_id           int         PRIMARY KEY,          -- Bitrix24 user id
    name              text        NOT NULL,
    active            boolean     NOT NULL DEFAULT true,
    max_open          int         NOT NULL DEFAULT 50,  -- capacity used for load-based balancing
    last_assigned_at  timestamptz
);

-- Most specific match wins (region and product both set > one set > '*'), then priority.
CREATE TABLE routing_rules (
    id           serial PRIMARY KEY,
    region       text   NOT NULL DEFAULT '*',   -- canonical lowercase region, see 02a "Normalize"
    product      text   NOT NULL DEFAULT '*',
    manager_ids  int[]  NOT NULL CHECK (cardinality(manager_ids) > 0),
    priority     int    NOT NULL DEFAULT 100,
    comment      text
);

CREATE TABLE assignments (
    inbox_id        bigint      PRIMARY KEY REFERENCES inbox (id),
    manager_id      int         NOT NULL REFERENCES managers (user_id),
    rule_id         int         REFERENCES routing_rules (id),
    reason          text        NOT NULL,
    assigned_at     timestamptz NOT NULL DEFAULT now(),
    deal_id         bigint,
    sla_item_id     bigint,
    crm_created_at  timestamptz,                  -- deal + task + SLA item exist in Bitrix24
    sla_due         timestamptz,
    sla_status      text        NOT NULL DEFAULT 'open' CHECK (sla_status IN ('open', 'met', 'breached')),
    sla_closed_at   timestamptz,
    escalated_at    timestamptz
);
CREATE INDEX assignments_open_idx ON assignments (manager_id) WHERE sla_status = 'open';
CREATE INDEX assignments_due_idx ON assignments (sla_due) WHERE sla_status = 'open';
CREATE UNIQUE INDEX assignments_deal_idx ON assignments (deal_id);

-- What the integration did with a lead and why (source for the deal tab in stage 4).
CREATE TABLE lead_events (
    id            bigserial   PRIMARY KEY,
    inbox_id      bigint      NOT NULL REFERENCES inbox (id),
    deal_id       bigint,
    at            timestamptz NOT NULL DEFAULT now(),
    step          text        NOT NULL,
    detail        jsonb       NOT NULL DEFAULT '{}',
    execution_id  text
);
CREATE INDEX lead_events_inbox_idx ON lead_events (inbox_id);
CREATE INDEX lead_events_deal_idx ON lead_events (deal_id);

CREATE TABLE dadata_cache (
    inn         text        PRIMARY KEY,
    found       boolean     NOT NULL,
    data        jsonb       NOT NULL,
    fetched_at  timestamptz NOT NULL DEFAULT now()
);

-- Routing decision, exactly once per inbox row (a retried lead keeps its manager).
-- Rule "own client -> own manager" first, then the most specific routing rule, then the least
-- loaded active manager of that rule (open SLAs / capacity, ties -> longest without a lead).
-- Candidate managers are locked, so concurrent workers cannot both pick the same "least loaded".
CREATE FUNCTION route_lead(p_inbox_id bigint, p_region text, p_product text, p_owner_id int)
RETURNS TABLE (manager_id int, rule_id int, reason text)
LANGUAGE plpgsql AS $$
DECLARE
    v_rule   routing_rules;
    v_mgr    int;
    v_reason text;
BEGIN
    RETURN QUERY SELECT a.manager_id, a.rule_id, a.reason FROM assignments a WHERE a.inbox_id = p_inbox_id;
    IF FOUND THEN
        RETURN;
    END IF;

    IF p_owner_id IS NOT NULL AND EXISTS (SELECT 1 FROM managers m WHERE m.user_id = p_owner_id AND m.active) THEN
        v_mgr := p_owner_id;
        v_reason := 'existing client: stays with its manager';
    ELSE
        SELECT r.* INTO v_rule FROM routing_rules r
         WHERE (r.region = '*' OR r.region = lower(coalesce(p_region, '')))
           AND (r.product = '*' OR r.product = lower(coalesce(p_product, '')))
         ORDER BY (r.region <> '*')::int + (r.product <> '*')::int DESC, r.priority, r.id
         LIMIT 1;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'no routing rule matches region=% product=%', p_region, p_product;
        END IF;

        PERFORM 1 FROM managers m WHERE m.user_id = ANY (v_rule.manager_ids) ORDER BY m.user_id FOR UPDATE;

        SELECT m.user_id INTO v_mgr
          FROM managers m
          LEFT JOIN LATERAL (SELECT count(*) AS open_cnt FROM assignments a
                              WHERE a.manager_id = m.user_id AND a.sla_status = 'open') l ON true
         WHERE m.user_id = ANY (v_rule.manager_ids) AND m.active
         ORDER BY l.open_cnt::numeric / m.max_open, m.last_assigned_at NULLS FIRST, m.user_id
         LIMIT 1;
        IF v_mgr IS NULL THEN
            RAISE EXCEPTION 'routing rule % has no active managers', v_rule.id;
        END IF;
        v_reason := format('rule #%s (region=%s, product=%s): least loaded of %s',
                           v_rule.id, v_rule.region, v_rule.product, v_rule.manager_ids);
    END IF;

    INSERT INTO assignments (inbox_id, manager_id, rule_id, reason)
    VALUES (p_inbox_id, v_mgr, v_rule.id, v_reason)
    ON CONFLICT (inbox_id) DO NOTHING;
    UPDATE managers SET last_assigned_at = clock_timestamp() WHERE user_id = v_mgr;

    RETURN QUERY SELECT a.manager_id, a.rule_id, a.reason FROM assignments a WHERE a.inbox_id = p_inbox_id;
END
$$;

INSERT INTO settings (key, value) VALUES
    ('process_batch_size',   '10'),
    ('process_max_attempts', '5'),
    ('sla_minutes_high',     '15'),
    ('sla_minutes_normal',   '60'),
    ('crm_title_prefix',     '')     -- '[DEMO] ' on the demo portal, set by seed_config.sh
ON CONFLICT (key) DO NOTHING;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"app_user";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"app_user";
GRANT EXECUTE ON FUNCTION route_lead(bigint, text, text, int) TO :"app_user";
