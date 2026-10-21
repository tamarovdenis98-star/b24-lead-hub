# ADR 0001: Intake authenticates with a shared-secret header, not HMAC

**Status:** accepted (stage 1)

## Context
`[B24HUB] 01 intake` is a public HTTPS webhook. The brief allows HMAC or a shared secret.

## Decision
n8n Webhook node with **Header Auth** credential `B24HUB Intake Secret` (header `X-B24Hub-Secret`).

## Why
- The check runs in the n8n webhook layer *before* an execution is created: a flood of
  unauthenticated requests costs no executions, DB rows or alerts (verified: 403, no execution).
- The secret lives only in n8n Credentials; the workflow export contains the credential *name*.
- HMAC needs the signing key inside a Code node. On n8n CE that means either hard-coding it in the
  workflow (leaks into git) or enabling `crypto` / `$env` access for the task runners, which changes
  the shared n8n deployment. Transport integrity is already provided by TLS.

## Consequences
- No body-integrity or replay-window protection beyond TLS. Replays are harmless:
  the idempotency key turns a replayed request into a no-op (`duplicate: true`).
- Upgrade path: HMAC verification in the reverse proxy in front of the webhook, no workflow change.
