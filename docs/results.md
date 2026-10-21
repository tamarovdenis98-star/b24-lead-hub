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
