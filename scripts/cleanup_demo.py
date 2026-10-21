#!/usr/bin/env python3
"""Deletes demo data created by b24-lead-hub on a test portal.

Only entities that carry BOTH markers are touched:
  * ORIGINATOR_ID = 'b24hub' (deals, contacts, companies), and
  * a title / name starting with the prefix (default "[DEMO]") — for tasks and SLA items this is the only marker.

--keep-deals keeps the listed deals together with everything attached to them (their contact, company,
task, SLA item); everything else with the markers is deleted.

  python scripts/cleanup_demo.py                              # dry run: counts
  python scripts/cleanup_demo.py --keep-deals 42,56 --list    # dry run: counts + ids to delete
  python scripts/cleanup_demo.py --keep-deals 42,56 --yes --deleted-deals-out deleted.txt
Env: B24_WEBHOOK. The Postgres journal is not touched (see docs/runbook.md).
"""
from __future__ import annotations

import argparse
from pathlib import Path

from b24 import B24

SLA_TITLE = "Контроль SLA"


def chunks(xs, n=50):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="actually delete")
    ap.add_argument("--prefix", default="[DEMO]")
    ap.add_argument("--keep-deals", default="", help="comma-separated deal ids to keep, or @file")
    ap.add_argument("--list", action="store_true", help="print the ids that would be deleted")
    ap.add_argument("--deleted-deals-out", help="write deleted deal ids to this file")
    args = ap.parse_args()
    b = B24()
    pfx = args.prefix
    raw = Path(args.keep_deals[1:]).read_text().strip() if args.keep_deals.startswith("@") else args.keep_deals
    keep = {int(x) for x in raw.split(",") if x.strip()}

    deals = [d for d in b.list_all("crm.deal.list", {"filter": {"ORIGINATOR_ID": "b24hub"},
                                                     "select": ["ID", "TITLE", "CONTACT_ID", "COMPANY_ID"]})
             if str(d["TITLE"] or "").startswith(pfx)]
    missing = keep - {int(d["ID"]) for d in deals}
    if missing:
        raise SystemExit(f"--keep-deals not found among demo deals: {sorted(missing)}")
    kept = [d for d in deals if int(d["ID"]) in keep]
    keep_contacts = {int(d["CONTACT_ID"]) for d in kept if d["CONTACT_ID"]}
    keep_companies = {int(d["COMPANY_ID"]) for d in kept if d["COMPANY_ID"]}

    found: dict[str, list[str]] = {}
    types = b.result("crm.type.list", {"filter": {"title": SLA_TITLE}})["types"]
    if types:
        etid = types[0]["entityTypeId"]
        items = b.list_all("crm.item.list", {"entityTypeId": etid, "select": ["id", "title", "parentId2"]}, key="items")
        found[f"crm.item.delete?entityTypeId={etid}&id="] = [
            str(i["id"]) for i in items if str(i["title"]).startswith(pfx) and int(i.get("parentId2") or 0) not in keep]
    tasks = b.list_all("tasks.task.list", {"filter": {"%TITLE": pfx}, "select": ["ID", "TITLE", "UF_CRM_TASK"]}, key="tasks")
    found["tasks.task.delete?taskId="] = [
        str(t["id"]) for t in tasks if str(t["title"]).startswith(pfx)
        and not any(str(x) in {f"D_{k}" for k in keep} for x in (t.get("ufCrmTask") or []))]
    found["crm.deal.delete?id="] = [d["ID"] for d in deals if int(d["ID"]) not in keep]
    for entity, field, keep_ids in (("contact", "NAME", keep_contacts), ("company", "TITLE", keep_companies)):
        rows = b.list_all(f"crm.{entity}.list", {"filter": {"ORIGINATOR_ID": "b24hub"}, "select": ["ID", field]})
        found[f"crm.{entity}.delete?id="] = [r["ID"] for r in rows
                                             if str(r[field] or "").startswith(pfx) and int(r["ID"]) not in keep_ids]

    print(f"keep: {len(kept)} deals, {len(keep_contacts)} contacts, {len(keep_companies)} companies (+ their tasks and SLA items)")
    for method, ids in found.items():
        print(f"delete {method.split('?')[0]:<20} {len(ids):>5}" + (f"  ids: {','.join(ids)}" if args.list else ""))
    if args.deleted_deals_out:
        Path(args.deleted_deals_out).write_text(",".join(found["crm.deal.delete?id="]))
    if not args.yes:
        print("dry run: nothing deleted (pass --yes)")
        return
    deleted = 0
    for method, ids in found.items():  # SLA items and tasks first, then deals, contacts, companies
        for part in chunks(ids):
            b.batch({f"d{i}": method + x for i, x in enumerate(part)})
            deleted += len(part)
    print(f"deleted {deleted} entities in {b.calls} REST calls")


if __name__ == "__main__":
    main()
