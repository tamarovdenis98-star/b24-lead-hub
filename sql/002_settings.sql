-- 002: runtime settings that are environment-specific (not secrets, but must not live in
-- workflow exports either: chat ids, thresholds). Seeded by scripts/seed_config.sh from .env.
CREATE TABLE settings (
    key         text PRIMARY KEY,
    value       text NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO settings (key, value) VALUES
    ('alert_suppress_after', '3'),     -- per workflow+node, within alert_window_min
    ('alert_window_min',     '5');
