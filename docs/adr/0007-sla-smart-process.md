# ADR 0007: SLA lives in a smart process, the deadline of record in Postgres

**Status:** accepted (stage 2)

## Decision
- Smart process «Контроль SLA» (created by `scripts/b24_setup.py` with `crm.type.add`, parent relation to
  deals): one item per lead, linked to the deal via `parentId2`, assignee = manager, observer = head of sales.
  Stages: «Ожидает первого ответа» → «Ответ в срок» / «SLA нарушен».
- SLA: 15 min for urgent leads, 60 min otherwise (`settings.sla_minutes_*`, calendar time).
- The deadline of record is `assignments.sla_due` in Postgres; the watcher (stage 3) reads it from there.
  The smart process only has date fields (`begindate` / `closedate`, no time), so the item title carries
  the exact time ("SLA до 14:35 21.10 — …"). With the webhook scope `userfieldconfig`, `b24_setup.py`
  adds a datetime field and the workflow fills it too (`settings.sla_due_field`).

## Consequences
Business hours are not taken into account (a known simplification, documented in the README).
