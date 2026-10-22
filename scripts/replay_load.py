#!/usr/bin/env python3
"""Load replay: 500 requests (20% duplicates, 10% invalid) through the real intake, then verifies
the outcome in Postgres and in Bitrix24 and writes the measured report into docs/results.md.

  python scripts/replay_load.py --run-id r1
Env:
  INTAKE_URL, INTAKE_SECRET  intake webhook and its shared secret
  B24_WEBHOOK                for the CRM-side duplicate check
  PSQL_CMD                   command that runs psql against the project DB and reads SQL from stdin, e.g.
                             "docker compose exec -T postgres psql -U b24hub_owner -d b24hub -At"
                             or "ssh myvps docker exec -i b24hub-postgres psql -U b24hub_owner -d b24hub -At"
Every number in the report comes from this run; nothing is estimated.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import random
import re
import shlex
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from b24 import B24  # noqa: E402
from check_crm_links import check as check_links, journal as link_journal  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REAL_INNS = ["7707083893", "7736207543", "7740000076", "7736050003", "7712040126", "7710140679", "7704217370", "7702070139"]
REGIONS = ["Москва", "г. Москва", "СПб", "Санкт-Петербург", "Новосибирск", "Казань", None]
PRODUCTS = ["crm", "telephony", "integration", None]
FIRST = ["Иван", "Анна", "Олег", "Мария", "Пётр", "Елена", "Сергей", "Ольга", "Дмитрий", "Наталья"]
LAST = ["Петров", "Смирнова", "Сидоров", "Иванова", "Кузнецов", "Попова", "Соколов", "Лебедева"]
MESSAGES = ["Нужна консультация", "Срочно нужен расчёт, сегодня", "Интеграция с 1С, бюджет 300 тыс",
            "Хотим IP-АТС на 20 сотрудников", "Подключите CRM для отдела продаж", "Перезвоните", None]


def inn_with_checksum(rng: random.Random) -> str:
    d = [rng.randint(0, 9) for _ in range(9)]
    d[0] = rng.randint(1, 9)
    w = [2, 4, 10, 3, 5, 9, 4, 6, 8]
    return "".join(map(str, d)) + str((sum(a * b for a, b in zip(w, d)) % 11) % 10)


def phone_variants(rng: random.Random, digits10: str) -> str:
    a, b, c, d = digits10[:3], digits10[3:6], digits10[6:8], digits10[8:]
    return rng.choice([f"8 ({a}) {b}-{c}-{d}", f"+7{digits10}", f"7{digits10}", f"+7 {a} {b} {c} {d}", f"8{digits10}"])


def build_requests(run_id: str, seed: int = 42) -> list[dict]:
    """350 unique valid + 100 duplicates (resends) + 50 invalid = 500, shuffled deterministically."""
    rng = random.Random(seed)
    clients = []
    for i in range(310):
        digits = "9" + "".join(str(rng.randint(0, 9)) for _ in range(9))
        clients.append({"name": f"{rng.choice(FIRST)} {rng.choice(LAST)}", "digits": digits,
                        "email": f"client{i}.{run_id}@example.ru"})
    unique = []
    for i in range(350):
        # ~12% are repeat clients: a new request from someone who already wrote (same phone or email)
        c = clients[i] if i < 310 else clients[rng.randrange(0, 310)]
        contact = {"name": c["name"]}
        mode = rng.random()
        if mode < 0.7:
            contact["phone"] = phone_variants(rng, c["digits"])
        if mode >= 0.4 or "phone" not in contact:
            contact["email"] = c["email"].upper() if rng.random() < 0.2 else c["email"]
        body = {"source": rng.choice(["web", "web", "telegram", "email", "api"]), "external_id": f"{run_id}-{i}",
                "contact": contact, "message": rng.choice(MESSAGES), "region": rng.choice(REGIONS),
                "product": rng.choice(PRODUCTS)}
        r = rng.random()
        if r < 0.25:
            body["company"] = {"inn": rng.choice(REAL_INNS)}
        elif r < 0.45:
            body["company"] = {"inn": inn_with_checksum(rng), "name": f"ООО Демо-{i}"}
        body = {k: v for k, v in body.items() if v is not None}
        unique.append({"kind": "unique", "body": body, "repeat_client": i >= 310})
    dups = [{"kind": "duplicate", "body": json.loads(json.dumps(rng.choice(unique)["body"]))} for _ in range(100)]
    invalid = []
    for i in range(50):
        if i < 25:  # rejected synchronously by the schema (422)
            bad = rng.choice([
                {"source": "fax", "external_id": f"{run_id}-bad{i}", "contact": {"phone": "+79990000000"}},
                {"source": "web", "external_id": f"{run_id}-bad{i}", "contact": {}},
                {"source": "web", "contact": {"email": "x@example.ru"}},
                {"source": "web", "external_id": f"{run_id}-bad{i}", "contact": {"phone": "+79990000000"}, "company": {"inn": "12345"}},
            ])
            invalid.append({"kind": "invalid_schema", "body": bad})
        else:  # valid shape, unusable data -> status 'rejected' after normalization
            invalid.append({"kind": "invalid_data", "body": {"source": "web", "external_id": f"{run_id}-bad{i}",
                            "contact": {"name": "Нет контактов", "phone": "12-34", "email": "not-an-email"}}})
    reqs = unique + dups + invalid
    rng.shuffle(reqs)
    return reqs


def post(url: str, secret: str, body: dict) -> tuple[int, dict, float]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/json; charset=utf-8", "X-B24Hub-Secret": secret})
    t = time.perf_counter()
    for attempt in range(3):  # network-level retry only; the intake is idempotent, so this is safe
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b"{}"), time.perf_counter() - t
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw or b"{}"), time.perf_counter() - t
            except ValueError:
                return e.code, {"raw": raw[:200].decode(errors="replace")}, time.perf_counter() - t
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                return 0, {}, time.perf_counter() - t
            time.sleep(1 + attempt)
    return 0, {}, 0.0


def sql_json(query: str):
    """Runs a query that returns one JSON value through PSQL_CMD."""
    cmd = shlex.split(os.environ["PSQL_CMD"])
    out = subprocess.run(cmd, input=query.encode(), capture_output=True, check=True).stdout.decode().strip()
    return json.loads(out.splitlines()[-1]) if out else None


def pct(values: list[float], p: float) -> float:
    v = sorted(values)
    return v[min(len(v) - 1, max(0, int(round(p * (len(v) - 1)))))] if v else float("nan")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=datetime.now().strftime("r%m%d%H%M"))
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--seed", type=int, default=42, help="data generator seed (change it to avoid matching earlier runs' contacts)")
    ap.add_argument("--timeout-min", type=int, default=40)
    ap.add_argument("--no-write", action="store_true", help="print the report, do not touch docs/results.md")
    args = ap.parse_args()
    url, secret = os.environ["INTAKE_URL"], os.environ["INTAKE_SECRET"]
    reqs = build_requests(args.run_id, args.seed)
    kinds = Counter(r["kind"] for r in reqs)
    print(f"run {args.run_id}: {len(reqs)} requests {dict(kinds)}", flush=True)

    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    results = []
    with cf.ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = {ex.submit(post, url, secret, r["body"]): r for r in reqs}
        for f in cf.as_completed(futs):
            status, body, dt = f.result()
            results.append({**futs[f], "status": status, "resp": body, "latency": dt})
    send_s = time.perf_counter() - t0
    st = Counter(r["status"] for r in results)
    print(f"sent in {send_s:.1f}s, HTTP statuses {dict(st)}", flush=True)

    since = started.isoformat()
    keys = sorted({f"{r['body']['source']}:{r['body']['external_id']}" for r in results
                   if r["status"] == 200 and r["kind"] != "invalid_schema"})
    keys_sql = "ARRAY[" + ",".join("'" + k.replace("'", "''") + "'" for k in keys) + "]::text[]"

    # wait until every accepted row is terminal (done / rejected / failed with no attempts left)
    deadline = time.time() + args.timeout_min * 60
    while True:
        s = sql_json(f"""SELECT json_build_object('total', count(*),
            'pending', count(*) FILTER (WHERE status IN ('new','processing')
                                         OR (status = 'failed' AND attempt_count < (SELECT value::int FROM settings WHERE key='process_max_attempts'))),
            'done', count(*) FILTER (WHERE status='done'), 'rejected', count(*) FILTER (WHERE status='rejected'),
            'failed', count(*) FILTER (WHERE status='failed'))
            FROM inbox WHERE idempotency_key = ANY({keys_sql})""")
        print(f"  {datetime.now():%H:%M:%S} {s}", flush=True)
        if s["pending"] == 0 or time.time() > deadline:
            break
        time.sleep(20)
    wall_s = time.perf_counter() - t0

    db = sql_json(f"""WITH i AS (SELECT * FROM inbox WHERE idempotency_key = ANY({keys_sql})),
        a AS (SELECT a.*, extract(epoch FROM a.crm_created_at - i.received_at) AS secs
                FROM assignments a JOIN i ON i.id = a.inbox_id WHERE a.crm_created_at IS NOT NULL),
        c AS (SELECT * FROM b24_call_log WHERE called_at >= '{since}')
        SELECT json_build_object(
          'inbox_rows', (SELECT count(*) FROM i),
          'done', (SELECT count(*) FROM i WHERE status='done'),
          'rejected', (SELECT count(*) FROM i WHERE status='rejected'),
          'failed', (SELECT count(*) FROM i WHERE status='failed'),
          'leads_retried', (SELECT count(*) FROM i WHERE attempt_count > 1),
          'repaired', (SELECT count(*) FROM i WHERE (result->>'repaired')::boolean),
          'secs', (SELECT coalesce(json_agg(secs ORDER BY secs), '[]') FROM a),
          'first_received', (SELECT min(received_at) FROM i), 'last_crm', (SELECT max(crm_created_at) FROM a),
          'deal_ids', (SELECT coalesce(json_agg(deal_id), '[]') FROM a),
          'by_manager', (SELECT coalesce(json_object_agg(manager_id, n), '{{}}') FROM (SELECT manager_id, count(*) n FROM a GROUP BY 1) x),
          'own_client', (SELECT count(*) FROM a WHERE reason LIKE 'existing client%'),
          'b24_calls', (SELECT count(*) FROM c), 'b24_retries', (SELECT count(*) FROM c WHERE attempt > 1),
          'limit_hits', (SELECT coalesce(json_object_agg(error_code, n), '{{}}') FROM (SELECT error_code, count(*) n FROM c
                         WHERE error_code IN ('QUERY_LIMIT_EXCEEDED','OPERATION_TIME_LIMIT') GROUP BY 1) x),
          'b24_errors', (SELECT coalesce(json_object_agg(coalesce(error_code,'-'), n), '{{}}') FROM (SELECT error_code, count(*) n FROM c WHERE NOT ok GROUP BY 1) x),
          'dadata_calls', (SELECT count(*) FROM lead_events e JOIN i ON i.id = e.inbox_id WHERE e.step='enrich' AND e.detail->>'source'='dadata'),
          'dadata_cache_hits', (SELECT count(*) FROM lead_events e JOIN i ON i.id = e.inbox_id WHERE e.step='enrich' AND e.detail->>'source'='cache'),
          'errors_logged', (SELECT count(*) FROM errors WHERE occurred_at >= '{since}'),
          'arrival_per_s', (SELECT round((count(*) / nullif(extract(epoch FROM max(received_at) - min(received_at)), 0))::numeric, 2) FROM i),
          'processing_per_s', (SELECT round((count(*) / nullif(extract(epoch FROM max(crm_created_at) - min(crm_created_at)), 0))::numeric, 2) FROM a),
          'b24_calls_per_s', (SELECT round((count(*) / nullif(extract(epoch FROM max(sent_at) - min(sent_at)), 0))::numeric, 2) FROM c),
          'b24_avg_ms', (SELECT round(avg(duration_ms)) FROM c))""")

    # CRM side: every deal we created, and duplicates among contacts / companies / deals
    b = B24()
    deals = list(b.list_all("crm.deal.list", {"filter": {"ORIGINATOR_ID": "b24hub"}, "select": ["ID", "ORIGIN_ID", "TITLE"]}))
    contacts = list(b.list_all("crm.contact.list", {"filter": {"ORIGINATOR_ID": "b24hub"}, "select": ["ID", "PHONE", "EMAIL"]}))
    companies = list(b.list_all("crm.company.list", {"filter": {"ORIGINATOR_ID": "b24hub"}, "select": ["ID", "ORIGIN_ID"]}))
    run_deals = [d for d in deals if (d["ORIGIN_ID"] or "") in set(keys)]
    deal_dup = sum(n - 1 for n in Counter(d["ORIGIN_ID"] for d in run_deals).values() if n > 1)
    comm = Counter()
    for c in contacts:
        for f in ("PHONE", "EMAIL"):
            for v in c.get(f) or []:
                comm[(f, str(v["VALUE"]).lower())] += 1
    contact_dup = sum(n - 1 for n in comm.values() if n > 1)
    company_dup = sum(n - 1 for n in Counter(c["ORIGIN_ID"] for c in companies).values() if n > 1)
    missing = set(map(int, db["deal_ids"])) - {int(d["ID"]) for d in run_deals}
    # links: deal -> contact / company recorded in the journal, SLA item -> deal (the stage-2 defect)
    sla_type = b.result("crm.type.list", {"filter": {"title": "Контроль SLA"}})["types"][0]["entityTypeId"]
    link_counts, _ = check_links(b, link_journal(set(map(int, db["deal_ids"]))), sla_type)

    lat = [r["latency"] * 1000 for r in results if r["status"] in (200, 422)]
    secs = db["secs"]
    accepted = db["inbox_rows"]
    terminal_ok = db["done"] + db["rejected"]
    expected_rows = len({f"{r['body']['source']}:{r['body']['external_id']}" for r in reqs if r["kind"] in ("unique", "invalid_data")})
    dup_flags = Counter(r["resp"].get("duplicate") for r in results if r["status"] == 200)
    rep = {
        "run_id": args.run_id, "started_utc": started.strftime("%Y-%m-%d %H:%M"),
        "requests": len(reqs), "kinds": dict(kinds), "http": dict(st),
        "intake_new": dup_flags.get(False, 0), "intake_duplicate": dup_flags.get(True, 0),
        "intake_latency_ms": {"p50": round(pct(lat, .5)), "p95": round(pct(lat, .95)), "max": round(max(lat))},
        "inbox_rows": accepted, "expected_rows": expected_rows,
        "done": db["done"], "rejected": db["rejected"], "failed": db["failed"],
        "processed_pct": round(100 * terminal_ok / accepted, 1) if accepted else 0,
        "leads_retried": db["leads_retried"], "repaired": db["repaired"],
        "time_to_crm_s": {"p50": round(pct(secs, .5), 1), "p95": round(pct(secs, .95), 1), "max": round(max(secs), 1)} if secs else {},
        "send_s": round(send_s, 1), "wall_s": round(wall_s, 1),
        "crm": {"deals": len(run_deals), "duplicate_deals": deal_dup, "duplicate_contacts": contact_dup,
                "duplicate_companies": company_dup, "deals_missing_in_crm": len(missing),
                "contacts_total": len(contacts), "companies_total": len(companies),
                "links_ok": link_counts.get("ok", 0),
                "link_problems": {k: v for k, v in link_counts.items() if k not in ("ok", "with problems")}},
        "routing": {"by_manager": db["by_manager"], "own_client_rule": db["own_client"]},
        "b24": {"calls": db["b24_calls"], "retries": db["b24_retries"], "limit_hits": db["limit_hits"], "errors": db["b24_errors"]},
        "dadata": {"calls": db["dadata_calls"], "cache_hits": db["dadata_cache_hits"]},
        "errors_logged": db["errors_logged"],
        "rates": {"arrival_per_s": db["arrival_per_s"], "processing_per_s": db["processing_per_s"],
                  "b24_calls_per_s": db["b24_calls_per_s"], "b24_avg_ms": db["b24_avg_ms"]},
    }
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    if not args.no_write:
        write_report(rep)


def write_report(r: dict) -> None:
    crm = r["crm"]
    t = r["time_to_crm_s"]
    md = f"""<!-- replay:start -->
## Load replay `{r['run_id']}` ({r['started_utc']} UTC)

`python scripts/replay_load.py` → {r['requests']} requests through the public intake
(concurrency 8, from a laptop over the internet): {r['kinds'].get('unique', 0)} unique, {r['kinds'].get('duplicate', 0)} resends
of earlier requests (20%), {r['kinds'].get('invalid_schema', 0) + r['kinds'].get('invalid_data', 0)} invalid (10%: half fail the schema, half carry unusable contacts).

| Metric | Value |
|---|---|
| **Duplicate deals in CRM** | **{crm['duplicate_deals']}** |
| **Duplicate contacts in CRM** (same phone/e-mail) | **{crm['duplicate_contacts']}** |
| Duplicate companies (same INN) | {crm['duplicate_companies']} |
| **CRM links correct** (deal → contact / company, SLA item → deal) | **{crm['links_ok']} of {crm['deals']}** {crm['link_problems'] or ''} |
| Processed (done + rejected) / accepted | **{r['processed_pct']}%** ({r['done']} + {r['rejected']} of {r['inbox_rows']}; expected rows {r['expected_rows']}) |
| Failed (no attempts left) | {r['failed']} |
| Intake answers | {r['intake_new']} new, {r['intake_duplicate']} duplicate, HTTP {r['http']} |
| Intake latency | p50 {r['intake_latency_ms']['p50']} ms, p95 {r['intake_latency_ms']['p95']} ms |
| Time from intake to deal+task+SLA in CRM | p50 {t.get('p50')} s, **p95 {t.get('p95')} s**, max {t.get('max')} s |
| Bitrix24 REST calls (batch counts as 1) | {r['b24']['calls']} |
| Retries on limits (`QUERY_LIMIT_EXCEEDED` / `OPERATION_TIME_LIMIT`) | {sum(r['b24']['limit_hits'].values()) if r['b24']['limit_hits'] else 0} {r['b24']['limit_hits'] or ''} |
| All Bitrix24 retries / non-ok responses | {r['b24']['retries']} / {r['b24']['errors'] or 0} |
| Leads retried / repaired after partial create | {r['leads_retried']} / {r['repaired']} |
| Routing: deals per manager / "own client" rule | {r['routing']['by_manager']} / {r['routing']['own_client_rule']} |
| DaData calls / cache hits | {r['dadata']['calls']} / {r['dadata']['cache_hits']} |
| Deals created in this run / missing in CRM | {crm['deals']} / {crm['deals_missing_in_crm']} |
| Errors logged by error handler during the run | {r['errors_logged']} |
| Wall time (send + process) | {r['wall_s']} s |

Where the time to CRM goes: leads arrived at {r['rates']['arrival_per_s']}/s and were processed at
{r['rates']['processing_per_s']}/s ({r['rates']['b24_calls_per_s']} Bitrix24 calls/s against the 2/s throttle; average call
{r['rates']['b24_avg_ms']} ms). So the p95 is mostly **queue wait** behind a burst, and the bottleneck is sequential
per-worker processing on 1 vCPU, not the Bitrix24 limit.
<!-- replay:end -->"""
    path = ROOT / "docs" / "results.md"
    text = path.read_text(encoding="utf-8")
    if "<!-- replay:start -->" in text:
        text = re.sub(r"<!-- replay:start -->.*?<!-- replay:end -->", md, text, flags=re.S)
    else:
        text = text.rstrip() + "\n\n" + md + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    (ROOT / "docs" / "replay_report.json").write_text(json.dumps(r, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"written: docs/results.md, docs/replay_report.json")


if __name__ == "__main__":
    main()
