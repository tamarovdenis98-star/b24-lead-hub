# ADR 0009: Deal updates arrive by event and by polling; both close SLAs through one library

**Status:** accepted (stage 3)

## Context
The SLA closes when the manager moves the deal off its first stage. Bitrix24 can push
`ONCRMDEALUPDATE` (outbound webhook or `event.bind` of an app), but events can be lost, and on the
demo portal the outbound-webhook form would not save. The handler was verified to answer in
0.4–0.6 s with 200, and no request from Bitrix24 reached it while the form was saving.

## Decision
- `[B24HUB] lib sla_close` is the only place that closes an SLA: it re-checks that the SLA is still open,
  decides `met` / `breached` by **`MOVED_TIME`** of the deal (when the stage changed, not when we noticed),
  moves the SLA item («Ответ в срок» / «SLA нарушен»), and journals `sla_closed` with `by: ONCRMDEALUPDATE | poller`.
- `[B24HUB] 04 b24 events` (push): token check, `crm.deal.get`, `lib sla_close`. In stage 4 the local app
  subscribes it with `event.bind`, so no outbound webhook is needed.
- `[B24HUB] 04b deal poller` (safety net, every minute): open SLAs from Postgres, one
  `crm.deal.list` per 50 of them with `filter: {@ID: [...], !STAGE_ID: NEW}`, `lib sla_close` for each hit.
  No watermark on `DATE_MODIFY`: only deals with open SLAs are asked about, so no edit can be missed.
  Cost: ⌈open SLAs / 50⌉ calls per minute.
- The watcher marks `breached` only where `sla_closed_at IS NULL`, so an SLA closed meanwhile keeps its status.

## Consequences
Without push events an SLA closes up to 1 minute late; the status is still exact (by `MOVED_TIME`).
A deal answered less than a minute before its deadline can still be escalated by the watcher before the
poller sees it; the SLA then closes as `met` anyway.

## Verified (stage 3)
Deal moved without any event → closed `met` by the poller (deal 786). Overdue, already-escalated deal
moved → `breached` (788). Deal closed by the event → the poller did nothing more (792, one `sla_closed`
row). Deal left in NEW → untouched (790).
