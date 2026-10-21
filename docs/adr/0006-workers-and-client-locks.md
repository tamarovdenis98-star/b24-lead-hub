# ADR 0006: Polling workers with SKIP LOCKED, serialized per client

**Status:** accepted (stage 2)

## Decision
- `[B24HUB] 02 process` runs every 15 s and claims up to `process_batch_size` (10) rows with
  `FOR UPDATE SKIP LOCKED`, setting `status = processing`, `attempt_count + 1`. Ticks overlap when a batch
  takes longer than 15 s, so several workers run concurrently without picking the same row.
  A row stuck in `processing` for 10 minutes (crashed worker) is claimed again.
- Each lead runs as its own execution of `[B24HUB] 02a process lead`, so every lead has an execution id
  (persisted) while empty ticks are not persisted.
- A failed lead goes to `failed` with backoff 1, 2, 4, 8 min and becomes terminal after
  `process_max_attempts` (5). `rejected` means bad data and is never retried.
- **Per-client lock.** Two leads of the same client handled by two workers at once would both miss the
  contact and both create it. Before touching CRM a worker takes `client_locks` for `phone:…`,
  `email:…` and `inn:…` of its lead. If any key is held, the lead is deferred for 20 s **without spending
  an attempt**. Locks are released when the lead finishes (done / failed) and expire after 5 minutes.

## Why not an advisory lock
n8n's Postgres node uses pooled connections, so a session lock cannot be held across the several nodes
and HTTP calls between lookup and create. A lock row with an expiry survives that and is visible for debugging.

## Verified
With a lock held by hand on `phone:+79031112233`, a new lead for that phone was deferred twice, kept
`attempt_count = 0`, then finished as soon as the lock was released and reused the existing contact.
