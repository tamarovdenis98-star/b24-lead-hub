# ADR 0005: Idempotency does not stop at the inbox — CRM writes are idempotent too

**Status:** accepted (stage 2)

## Context
The `inbox` unique key makes a *request* idempotent. Processing can still fail half-way:
Bitrix24 has no transactions, and a `batch` with `halt=1` keeps the commands that ran before the
failing one (verified on the portal: the deal created before a failing `crm.item.add` stayed).
In another observed case (inbox #5) a batch failing at its 4th command applied none of them, so the
outcome of a failed batch cannot be predicted. A retry that simply runs the same batch again could
create a second deal.

## Decision
1. Every entity the hub creates carries `ORIGINATOR_ID = b24hub`. A deal carries
   `ORIGIN_ID = inbox.idempotency_key`; a company carries `ORIGIN_ID = INN`.
2. The first call for every lead is a lookup batch: `crm.duplicate.findbycomm` by phone and by e-mail,
   the matched contact's owner, the company by INN (`crm.requisite.list`), and **our own deal by ORIGIN_ID**.
3. If the deal already exists, a repair lookup checks the task (`UF_CRM_TASK = D_<id>`), the SLA item
   (`parentId2`) and the timeline comment (marker `[b24hub]`). Only the missing ones are created, and the
   deal keeps its current assignee.
4. Routing is stored once per inbox row (`assignments.inbox_id` PK): a retried lead keeps its manager.

## Verified (stage 2)
- A failed SLA-item create left the lead `failed` with a 1-minute backoff, and the next attempt
  finished it with one deal (inbox #5, executions 421 → 429).
- An orphan deal with the lead's ORIGIN_ID, created by hand to simulate a crash, was found and completed
  with task, SLA item and comment; no second deal was created (inbox #6, `repaired: true`).
