-- 004: reserve a throttle slot and return its absolute time. Callers sleep until that instant
-- (not for a precomputed duration), so scheduling jitter between reserve and sleep does not
-- compress the gaps between requests.
CREATE FUNCTION rate_limit_reserve(p_key text) RETURNS timestamptz
LANGUAGE sql AS $$
    UPDATE rate_limiter
       SET next_slot = GREATEST(next_slot, clock_timestamp()) + make_interval(secs => interval_ms / 1000.0)
     WHERE key = p_key
 RETURNING next_slot - make_interval(secs => interval_ms / 1000.0);
$$;
GRANT EXECUTE ON FUNCTION rate_limit_reserve(text) TO :"app_user";
