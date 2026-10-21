-- 007: one rule per (region, product), so seeds can upsert instead of delete+insert
-- (assignments reference routing_rules, deleting a used rule is not allowed).
CREATE UNIQUE INDEX routing_rules_region_product_idx ON routing_rules (region, product);
