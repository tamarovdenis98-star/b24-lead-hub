#!/usr/bin/env python3
"""Deletes demo data created by b24-lead-hub on a test portal.

Only entities that carry BOTH markers are touched:
  * ORIGINATOR_ID = 'b24hub' (deals, contacts, companies), and
  * a title / name starting with the prefix (default "[DEMO]") — for tasks and SLA items this is the only marker.

  python scripts/cleanup_demo.py            # dry run: counts only
  python scripts/cleanup_demo.py --yes      # delete
Env: B24_WEBHOOK. Postgres journal is not touched (see docs/runbook.md to reset it).
"""
from __future__ import annotations

import argparse

from b24 import B24

SLA_TITLE = "Контроль SLA"


def chunks(xs, n=50):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="actually delete")
    ap.add_argument("--prefix", default="[DEMO]")
    args = ap.parse_args()
    b = B24()
    pfx = args.prefix

    found: dict[str, list[str]] = {}
    types = b.result("crm.type.list", {"filter": {"title": SLA_TITLE}})["types"]
    if types:
        etid = types[0]["entityTypeId"]
        items = b.list_all("crm.item.list", {"entityTypeId": etid, "select": ["id", "title"]}, key="items")
        found[f"crm.item.delete?entityTypeId={etid}&id="] = [str(i["id"]) for i in items if str(i["title"]).startswith(pfx)]
    tasks = b.list_all("tasks.task.list", {"filter": {"%TITLE": pfx}, "select": ["ID", "TITLE"]}, key="tasks")
    found["tasks.task.delete?taskId="] = [str(t["id"]) for t in tasks if str(t["title"]).startswith(pfx)]
    for entity, field in (("deal", "TITLE"), ("contact", "NAME"), ("company", "TITLE")):
        rows = b.list_all(f"crm.{entity}.list", {"filter": {"ORIGINATOR_ID": "b24hub"}, "select": ["ID", field]})
        found[f"crm.{entity}.delete?id="] = [r["ID"] for r in rows if str(r[field] or "").startswith(pfx)]

    for method, ids in found.items():
        print(f"{method.split('?')[0]:<20} {len(ids):>5}")
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
