# Results

All numbers below were measured, not estimated. Execution ids refer to the n8n instance
`n8n.144-31-229-139.sslip.io` (n8n 2.43.4 CE, 1 vCPU / 2 GB VPS).

## Stage 1 — intake, REST wrapper, error handling (2026-10-21)

### `[B24HUB] 01 intake`
| Case | Request | Response | Execution |
|---|---|---|---|
| new lead | valid body, `external_id=form-…` | `200 {"duplicate":false,"inbox_id":1}` | 289 |
| same request again | identical body | `200 {"duplicate":true,"inbox_id":1}` | 290 |
| key from header | `Idempotency-Key: tg-msg-…`, no `external_id` | `200 {"duplicate":false}` | 291 |
| invalid schema | bad source, empty name, 5-digit INN, no id | `422` + 5 errors, row in `intake_rejects` | 292 |
| no / wrong secret | — | `403`, no execution created | — |
| malformed JSON | truncated body | `422 Failed to parse request body`, no execution | — |
| race | 30 identical requests in parallel | 1 × `duplicate:false`, 29 × `duplicate:true`, **1 row** in `inbox` | 30 executions, all success |

Latency, 50 sequential requests from a laptop over the internet (TLS + RTT included):
p50 **512 ms**, p95 **735 ms**, max 2818 ms (the first request after deploy is a cold start).

### `[B24HUB] lib b24_call` against the real portal
| Case | Result | Harness execution |
|---|---|---|
| `user.get` | `ok:true`, 1 attempt | 27 |
| unknown method | `ok:false`, `ERROR_METHOD_NOT_FOUND`, HTTP 404, not retried | 29 |
| `crm.deal.get` missing id | `ok:false`, HTTP 400 `Not found`, not retried | 31 |

Throttle, 20 parallel calls: 20/20 ok, sends spread over 9.47 s, average gap **498 ms**,
max 3 sends in any 1 s window, 0 `QUERY_LIMIT_EXCEEDED`.

Trying to provoke a real limit: 120 direct requests at 60 in parallel against the portal webhook
all returned 200. The trial portal's bucket is larger than the documented 50, so retries were
verified against the failure simulator instead.

### Retries, against `[B24HUB] 91 b24 mock` (same lib, `b24_auth.base_url` pointed at the mock)
| Scenario | Attempts | Gaps between attempts | Outcome | Execution |
|---|---|---|---|---|
| 3 × 503 `QUERY_LIMIT_EXCEEDED`, then ok | 4 | 1630 / 2424 / 4221 ms (backoff 1→2→4 s + throttle) | `ok:true` | 261 |
| 1 × 429 `OPERATION_TIME_LIMIT`, then ok | 2 | 3166 ms (waited until `operating_reset_at`) | `ok:true` | 267 |
| `{"error":"ACCESS_DENIED"}` with HTTP 200 | 1 | — | `ok:false`, not retried | 271 |
| limit never clears, `max_attempts=3` | 3 | 1304 / 2413 ms | `ok:false QUERY_LIMIT_EXCEEDED` | 274 |
| connection refused | n8n Retry On Fail, 3 tries (3.0 s total) | — | execution fails → error handler | 279 → 280 |

### `[B24HUB] 99 error handler`
| Case | Result | Executions |
|---|---|---|
| forced failure (`Stop and Error`) | row in `errors`, Telegram alert sent | 284 |
| failure inside sub-workflow | 2 rows in `errors`, **1** Telegram alert from the parent with a link to sub-execution 286; the sub's own alert is muted | 286, 287 (muted), 288 (sent) |

### Export hygiene
`scripts/export_workflows.sh`: deploy → export → deploy → export produces byte-identical files.
`scripts/check_exports.py` and a grep for the 8 real secret values in the tree: clean.

## Stage 2 — processing, functional checks (real portal, one lead at a time)
| Case | Result | 02a execution |
|---|---|---|
| Moscow lead with a real INN | company + requisite (DaData: ПАО СБЕРБАНК, ACTIVE) + contact + deal + task + SLA item + comment in **one batch**; rule #2, 6.7 s from intake to CRM | 380 |
| `+7 (921) 555-01-02 доб. 15`, bad INN checksum, "Срочно … 1С, бюджет 300 тыс" | phone → `+79215550102`, INN dropped with a warning, score `integration / high / 300000`, SLA 15 min | 397 |
| same client again by phone, from Telegram | contact reused (`matched_by: phone`), "own client" rule → same manager, new deal | 406 |
| phone `123`, e-mail `not-an-email` | `rejected`, never retried | 409 |
| SLA item create fails (bad entity type) | lead `failed`, retry in 1 min, alert; next attempt completes, **1 deal** | 421 → 429 |
| orphan deal with the lead's ORIGIN_ID created by hand (simulated crash) | found by ORIGIN_ID; only task + SLA item + comment created (`repaired: true`), no second deal | inbox #6 |
| lock held on the client's phone by "another worker" | lead deferred twice without spending attempts, finished after release, contact reused | inbox #3 (2nd set) |

Idle-system time from intake to CRM on these single leads: 6.7–14.9 s (one 15-second polling tick + two batch calls).

### Defect found after the stage-2 replay (fixed in stage 3)
`scripts/check_crm_links.py` showed that in the replay run **319 of 350 deals pointed to the wrong
contact, 85 to the wrong company, and no SLA item was linked to its deal**. No duplicates were created
(the numbers below stay true), but the links were wrong.

Cause: `lib b24_call` passed its input through Postgres as `jsonb`, and `jsonb` reorders object keys
(shorter first). The batch `cmd` `{company, requisite, contact, deal, …}` reached Bitrix24 as
`{sla, deal, task, …, contact}`, so `deal` ran before `$result[contact]` existed.
It was reproduced through n8n and not reproduced by the same body sent from Python.
Fix: the lib passes its state as `text` (`lib_b24_call.json`, node "Acquire slot"). The same 7-command
batch through n8n then linked contact, company and SLA item correctly (harness execution 2321).
The 40 deals kept for screenshots were repaired with `check_crm_links.py --fix` (40/40 ok).
The replay will be re-run with the link check in stage 5.

<!-- replay:start -->
## Stage 2 — load replay `r1021a` (2026-10-21 11:04 UTC)

`python scripts/replay_load.py` → 500 requests through the public intake
(concurrency 8, from a laptop over the internet): 350 unique, 100 resends
of earlier requests (20%), 50 invalid (10%: half fail the schema, half carry unusable contacts).

| Metric | Value |
|---|---|
| **Duplicate deals in CRM** | **0** |
| **Duplicate contacts in CRM** (same phone/e-mail) | **0** |
| Duplicate companies (same INN) | 0 |
| Processed (done + rejected) / accepted | **100.0%** (350 + 25 of 375; expected rows 375) |
| Failed (no attempts left) | 0 |
| Intake answers | 375 new, 100 duplicate, HTTP {'200': 475, '422': 25} |
| Intake latency | p50 2942 ms, p95 4542 ms |
| Time from intake to deal+task+SLA in CRM | p50 242.3 s, **p95 377.6 s**, max 551.7 s |
| Bitrix24 REST calls (batch counts as 1) | 700 |
| Retries on limits (`QUERY_LIMIT_EXCEEDED` / `OPERATION_TIME_LIMIT`) | 0  |
| All Bitrix24 retries / non-ok responses | 0 / 0 |
| Leads retried / repaired after partial create | 0 / 0 |
| Routing: deals per manager / "own client" rule | {'16': 116, '20': 117, '18': 117} / 101 |
| DaData calls / cache hits | 85 / 81 |
| Deals created in this run / missing in CRM | 350 / 0 |
| Errors logged by error handler during the run | 0 |
| Wall time (send + process) | 611.2 s |

Where the time to CRM goes: leads arrived at 1.91/s and were processed at
0.62/s (1.23 Bitrix24 calls/s against the 2/s throttle; average call
716 ms). So the p95 is mostly **queue wait** behind a burst, and the bottleneck is sequential
per-worker processing on 1 vCPU, not the Bitrix24 limit.
<!-- replay:end -->

## Stage 3 — SLA watcher and Bitrix24 events (2026-10-21)

### `[B24HUB] 03 sla watcher` (14 test leads, 12 made overdue by moving `sla_due`)
| Check | Result |
|---|---|
| dry run, before any test data (runs 1–2) | `idle`, due 0 — **0 escalations would be sent** |
| dry run with 12 overdue (run 4) | `dry_run`, due 12, planned 10 (limit), nothing escalated |
| breaker = 5 (runs 5–6) | `breaker` twice, **one** Telegram warning (gate at 12:12:23), 0 escalations |
| limit = 10 (runs 7–8) | 10 escalated at 12:14:23, 2 at 12:15:23; 12 `batch` calls ok; 12 SLA items → «SLA нарушен»; 2 in-time items untouched |

### `[B24HUB] 04 b24 events` (outbound-webhook format, form-urlencoded)
| Case | Result | Execution |
|---|---|---|
| wrong `auth[application_token]` | `403` | — |
| deal not created by the hub | `200`, ignored | — |
| our deal, still in stage NEW | `200`, SLA stays open | — |
| deal 782 → PREPARATION within SLA | SLA `met`, item → «Ответ в срок», response 6 min | 2513 |
| escalated deal 758 → EXECUTING | SLA closed as `breached` | 2516 |
| same event again | no change (SLA already closed) | — |
| `ONCRMDEALADD` | ignored | — |

Test deals were removed afterwards (`cleanup_demo.py --keep-deals`, 56 entities); the portal holds the 40 demo deals.
