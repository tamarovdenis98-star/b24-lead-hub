-- Demo routing table. Applied by scripts/seed_config.sh with psql vars :m1 :m2 :m3 (Bitrix24 user ids).
-- Regions are canonical lowercase names produced by 02a "Normalize" (e.g. "г Москва" -> "москва").
BEGIN;
DELETE FROM routing_rules WHERE comment LIKE 'demo:%';
INSERT INTO managers (user_id, name) VALUES
    (:m1, 'manager ' || :m1), (:m2, 'manager ' || :m2), (:m3, 'manager ' || :m3)
ON CONFLICT (user_id) DO NOTHING;
INSERT INTO routing_rules (region, product, manager_ids, priority, comment) VALUES
    ('москва',          'integration', ARRAY[:m2]::int[],           10,   'demo: Moscow integrations -> integration expert'),
    ('москва',          '*',           ARRAY[:m1, :m2]::int[],      100,  'demo: Moscow desk'),
    ('санкт-петербург', '*',           ARRAY[:m3]::int[],           100,  'demo: St. Petersburg office'),
    ('*',               'integration', ARRAY[:m2, :m3]::int[],      100,  'demo: integrations, any region'),
    ('*',               '*',           ARRAY[:m1, :m2, :m3]::int[], 1000, 'demo: fallback, everyone');
COMMIT;
