-- 009: Bitrix24 local application (OAuth). One row per portal (member_id).
-- Tokens are written by [B24HUB] 05 app install and on every refresh by [B24HUB] lib b24_app_token;
-- the refresh_token is replaced each time (Bitrix24 invalidates the previous one).
CREATE TABLE b24_app (
    member_id           text        PRIMARY KEY,
    domain              text        NOT NULL,
    client_endpoint     text        NOT NULL,              -- https://<domain>/rest/
    server_endpoint     text,
    access_token        text        NOT NULL,
    refresh_token       text        NOT NULL,
    expires_at          timestamptz NOT NULL,
    application_token   text        NOT NULL,              -- auth[application_token] of events / robot calls
    scope               text,
    status              text        NOT NULL DEFAULT 'installed' CHECK (status IN ('installed', 'uninstalled')),
    installed_at        timestamptz NOT NULL DEFAULT now(),
    install_report      jsonb,
    refresh_lock_until  timestamptz,                        -- one refresher at a time
    last_refresh_at     timestamptz,
    refresh_count       int         NOT NULL DEFAULT 0,
    updated_at          timestamptz NOT NULL DEFAULT now()
);

-- Every call of the robot "Проверить контрагента по ИНН".
CREATE TABLE robot_runs (
    id            bigserial   PRIMARY KEY,
    at            timestamptz NOT NULL DEFAULT now(),
    document_id   text        NOT NULL,                     -- e.g. DEAL_123
    inn           text,
    result        jsonb       NOT NULL,
    event_sent    boolean     NOT NULL DEFAULT false,
    execution_id  text
);
CREATE INDEX robot_runs_doc_idx ON robot_runs (document_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON b24_app, robot_runs TO :"app_user";
GRANT USAGE, SELECT ON SEQUENCE robot_runs_id_seq TO :"app_user";
