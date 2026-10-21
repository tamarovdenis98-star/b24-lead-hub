-- 006: per-client serialization. Two leads of the same client (same phone / e-mail / INN) processed
-- by two workers at once would both miss the contact in CRM and both create it. A worker takes all
-- keys of its lead or defers the lead; locks are released when the lead finishes and expire after
-- 5 minutes if a worker dies.
CREATE TABLE client_locks (
    comm_key   text        PRIMARY KEY,         -- 'phone:+79161234567', 'email:a@b.ru', 'inn:7707083893'
    inbox_id   bigint      NOT NULL REFERENCES inbox (id),
    locked_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX client_locks_inbox_idx ON client_locks (inbox_id);
GRANT SELECT, INSERT, UPDATE, DELETE ON client_locks TO :"app_user";
