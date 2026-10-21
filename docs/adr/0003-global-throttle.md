# ADR 0003: Global Bitrix24 throttle via Postgres slot reservation

**Status:** accepted (stage 1)

## Context
Bitrix24 REST uses a leaky bucket (documented: threshold 50, drain 2 req/s on non-Enterprise plans;
overflow gives HTTP 503 `QUERY_LIMIT_EXCEEDED`) and a per-method operating-time limit
(overflow gives HTTP 429 `OPERATION_TIME_LIMIT`). Several n8n executions call Bitrix24 at the same
time, so per-execution delays do not bound the total rate.

## Decision
- `rate_limit_reserve('b24')` atomically moves `rate_limiter.next_slot` forward by 500 ms and returns
  the reserved instant. The lib sleeps (Wait node) **until that absolute instant**, so all executions
  share one schedule.
- Retryable responses (`QUERY_LIMIT_EXCEEDED`, `OPERATION_TIME_LIMIT`, 429/5xx) get exponential backoff
  1 s, 2 s, 4 s… (cap 30 s, jitter up to 250 ms). For `OPERATION_TIME_LIMIT` the wait lasts at least until
  `time.operating_reset_at` (cap 60 s). Default 5 attempts.
- Network-level failures (connection refused, timeouts) are covered by n8n *Retry On Fail* on the HTTP
  node (3 tries, 1 s). HTTP errors never throw (`neverError`), so the two mechanisms do not stack.
  The HTTP node does not use `On Error: Continue`, so Max Tries is honoured.

## Measured (stage 1, real portal, 20 parallel calls)
Average gap between sends is 498 ms. Up to 3 sends can fall into one 1-second window: a late request
compresses the gap to the next one. The bucket (threshold 50) absorbs that, and no
`QUERY_LIMIT_EXCEEDED` was observed. Backoff behaviour was verified against `[B24HUB] 91 b24 mock`
(see docs/results.md).
