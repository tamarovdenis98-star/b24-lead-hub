# ADR 0004: One alert per incident, never silent

**Status:** accepted (stage 1)

## Decision
`[B24HUB] 99 error handler` is the Error Workflow of every project workflow.
1. Every failure is written to `errors`: workflow, execution id and URL, node, message, stack.
2. A failed sub-workflow surfaces twice: in its own execution (mode `integrated`) and in the parent.
   Only the parent alerts, and its message links to the sub-execution (`error.executionId`).
3. Per workflow and node: after `alert_suppress_after` (3) alerts within `alert_window_min` (5 min),
   further alerts are muted but still logged. A Bitrix24 outage must not produce 500 messages.
4. If Postgres is down, the alert is still sent. The chat id is cached in the workflow's static data
   on every successful run, and the alert says "not logged: Postgres unavailable".
5. The Telegram node has Retry On Fail (4 tries, 3 s).
