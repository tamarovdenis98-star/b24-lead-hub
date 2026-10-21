# ADR 0008: SLA watcher — dry run, per-run limit, breaker, one digest

**Status:** accepted (stage 3)

## Context
After the stage-2 load replay, 350 demo SLAs were overdue at once. A naive watcher would have sent
700 Bitrix24 notifications and 350 Telegram messages in its first minute.

## Decision
`[B24HUB] 03 sla watcher` runs every minute and is controlled by `settings`:

| Setting | Default | Effect |
|---|---|---|
| `sla_watch_dry_run` | `true` | New deployments only count and log to `sla_watch_runs`, nothing is sent |
| `sla_escalation_limit` | `10` | At most N escalations per run; the rest wait for the next minute (oldest first) |
| `sla_escalation_breaker` | `50` | If more SLAs than this are overdue at once, it is an incident (backlog, clock, outage): no individual escalations, one Telegram warning per hour |

An escalation = `im.notify.system.add` to the manager and to the head of sales, the SLA item moves to
«SLA нарушен», `assignments.sla_status = breached`. Telegram gets **one digest per run**, not one message per deal.
Rows are claimed with `escalated_at` before sending, so overlapping runs cannot escalate twice. A failed
escalation releases its claim and is retried in the next run.

SLAs are closed by `[B24HUB] 04 b24 events` (ONCRMDEALUPDATE → token check → `crm.deal.get`): the first
response is the deal leaving its first stage. Closed before the deadline → `met` («Ответ в срок»), otherwise `breached`.

## Verified (stage 3)
- Dry run with 12 overdue test SLAs: `due 12, planned 10`, nothing escalated.
- Breaker at 5: two runs in `breaker` mode, one warning (gate timestamp), zero escalations.
- Limit 10: 10 escalated at 12:14:23, the remaining 2 at 12:15:23; 12 SLA items in «SLA нарушен».
