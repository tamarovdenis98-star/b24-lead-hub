#!/usr/bin/env python3
"""Reconciles CRM links with the journal: for every processed lead the deal must point to the contact
and company recorded in inbox.result, the SLA item must be linked to the deal (parentId2), and the task
must be bound to the deal (UF_CRM_TASK = D_<id>).

  python scripts/check_crm_links.py                    # report
  python scripts/check_crm_links.py --deals 42,56 --fix
Env: B24_WEBHOOK, PSQL_CMD (see scripts/replay_load.py).
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
from collections import Counter

from b24 import B24


def journal(deal_ids: set[int] | None) -> list[dict]:
    where = f"AND a.deal_id = ANY(ARRAY[{','.join(map(str, deal_ids))}]::bigint[])" if deal_ids else ""
    q = f"""SELECT coalesce(json_agg(json_build_object(
              'deal', a.deal_id, 'contact', (i.result->>'contact_id')::bigint, 'company', (i.result->>'company_id')::bigint,
              'sla', a.sla_item_id, 'task', (i.result->>'task_id')::bigint)), '[]')
            FROM assignments a JOIN inbox i ON i.id = a.inbox_id WHERE a.deal_id IS NOT NULL {where}"""
    out = subprocess.run(shlex.split(os.environ["PSQL_CMD"]), input=q.encode(), capture_output=True, check=True)
    return json.loads(out.stdout.decode().strip().splitlines()[-1])


def check(b: B24, rows: list[dict], sla_type: int) -> tuple[Counter, list[dict]]:
    """Returns counters and the list of rows with problems."""
    ids = [r["deal"] for r in rows]
    deals, items = {}, {}
    for part in range(0, len(ids), 50):
        chunk = ids[part:part + 50]
        for d in b.list_all("crm.deal.list", {"filter": {"ID": chunk}, "select": ["ID", "CONTACT_ID", "COMPANY_ID"]}):
            deals[int(d["ID"])] = d
        for it in b.list_all("crm.item.list", {"entityTypeId": sla_type, "filter": {"@id": [r["sla"] for r in rows[part:part + 50]]},
                                                "select": ["id", "parentId2"]}, key="items"):
            items[int(it["id"])] = it
    c, bad = Counter(), []
    for r in rows:
        d = deals.get(r["deal"])
        if not d:
            c["deal missing"] += 1
            bad.append({**r, "problem": ["deal missing"]})
            continue
        problems = []
        if int(d["CONTACT_ID"] or 0) != (r["contact"] or 0):
            problems.append("contact")
        if int(d["COMPANY_ID"] or 0) != (r["company"] or 0):
            problems.append("company")
        if int(items.get(r["sla"], {}).get("parentId2") or 0) != r["deal"]:
            problems.append("sla_link")
        for p in problems:
            c[f"{p} wrong"] += 1
        c["ok" if not problems else "with problems"] += 1
        if problems:
            bad.append({**r, "problem": problems})
    return c, bad


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deals", default="")
    ap.add_argument("--fix", action="store_true")
    args = ap.parse_args()
    b = B24()
    sla_type = b.result("crm.type.list", {"filter": {"title": "Контроль SLA"}})["types"][0]["entityTypeId"]
    rows = journal({int(x) for x in args.deals.split(",") if x.strip()} or None)
    c, bad = check(b, rows, sla_type)
    print(f"checked {len(rows)} deals: {dict(c)}")
    if args.fix and bad:
        cmd = {}
        for r in bad:
            if {"contact", "company"} & set(r["problem"]):
                cmd[f"d{r['deal']}"] = f"crm.deal.update?id={r['deal']}&fields[CONTACT_ID]={r['contact'] or ''}&fields[COMPANY_ID]={r['company'] or ''}"
            if "sla_link" in r["problem"]:
                cmd[f"s{r['deal']}"] = f"crm.item.update?entityTypeId={sla_type}&id={r['sla']}&fields[parentId2]={r['deal']}"
        keys = list(cmd)
        for i in range(0, len(keys), 50):
            b.batch({k: cmd[k] for k in keys[i:i + 50]})
        c2, _ = check(b, rows, sla_type)
        print(f"after fix: {dict(c2)}")


if __name__ == "__main__":
    main()
