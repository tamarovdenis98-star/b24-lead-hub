#!/usr/bin/env python3
"""Idempotent portal setup: smart process "Контроль SLA" linked to deals, readable stage names,
optional datetime field for the SLA deadline. Prints settings for Postgres (`settings` table).

  python scripts/b24_setup.py            # create what is missing, print settings as SQL
Env: B24_WEBHOOK.
"""
from __future__ import annotations

import sys

from b24 import B24, B24Error

TITLE = "Контроль SLA"
STAGES = {  # semantic -> displayed name
    "waiting": "Ожидает первого ответа",
    "success": "Ответ в срок",
    "fail": "SLA нарушен",
}


def ensure_type(b: B24) -> dict:
    for t in b.result("crm.type.list", {"filter": {"title": TITLE}})["types"]:
        print(f"-- smart process exists: id={t['id']} entityTypeId={t['entityTypeId']}", file=sys.stderr)
        return t
    t = b.result("crm.type.add", {"fields": {
        "title": TITLE,
        "isStagesEnabled": "Y",
        "isCategoriesEnabled": "N",
        "isAutomationEnabled": "Y",
        "isBizProcEnabled": "Y",
        "isBeginCloseDatesEnabled": "Y",
        "isObserversEnabled": "Y",
        "isRecyclebinEnabled": "Y",
        "relations": {"parent": [{"entityTypeId": 2, "isChildrenListEnabled": "true"}]},
    }})["type"]
    print(f"-- smart process created: id={t['id']} entityTypeId={t['entityTypeId']}", file=sys.stderr)
    return t


def stages(b: B24, entity_type_id: int) -> dict[str, str]:
    cats = b.result("crm.category.list", {"entityTypeId": entity_type_id})["categories"]
    cat = next(c for c in cats if c.get("isDefault") in ("Y", True))
    entity_id = f"DYNAMIC_{entity_type_id}_STAGE_{cat['id']}"
    rows = b.result("crm.status.list", {"filter": {"ENTITY_ID": entity_id}, "order": {"SORT": "ASC"}})
    by_sem = {"waiting": rows[0]}
    for r in rows:
        if r.get("SEMANTICS") == "S":
            by_sem["success"] = r
        elif r.get("SEMANTICS") == "F":
            by_sem.setdefault("fail", r)
    for sem, row in by_sem.items():
        if row["NAME"] != STAGES[sem]:
            b.result("crm.status.update", {"id": row["ID"], "fields": {"NAME": STAGES[sem]}})
            print(f"-- stage {row['STATUS_ID']} renamed to {STAGES[sem]!r}", file=sys.stderr)
    return {sem: row["STATUS_ID"] for sem, row in by_sem.items()}


def ensure_due_field(b: B24, t: dict) -> str | None:
    """Datetime UF for the deadline. Needs webhook scope `userfieldconfig`; optional."""
    entity_id = f"CRM_{t['id']}"
    name = f"UF_{entity_id}_SLA_DUE"
    try:
        existing = b.result("userfieldconfig.list", {"moduleId": "crm", "filter": {"entityId": entity_id, "fieldName": name}})["fields"]
        if not existing:
            b.result("userfieldconfig.add", {"moduleId": "crm", "field": {
                "entityId": entity_id, "fieldName": name, "userTypeId": "datetime", "multiple": "N",
                "editFormLabel": {"ru": "Срок первого ответа", "en": "SLA due"},
                "listColumnLabel": {"ru": "Срок SLA", "en": "SLA due"}}})
            print(f"-- field {name} created", file=sys.stderr)
    except B24Error as e:
        if e.code in ("insufficient_scope", "INSUFFICIENT_SCOPE"):
            print("-- no `userfieldconfig` scope: deadline kept in Postgres + item closedate", file=sys.stderr)
            return None
        raise
    # camelCase name used by crm.item.*: UF_CRM_16_SLA_DUE -> ufCrm16SlaDue
    parts = name.lower().split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def main() -> None:
    b = B24()
    t = ensure_type(b)
    st = stages(b, t["entityTypeId"])
    due = ensure_due_field(b, t)
    settings = {
        "sla_entity_type_id": t["entityTypeId"],
        "sla_stage_waiting": st["waiting"],
        "sla_stage_success": st["success"],
        "sla_stage_fail": st["fail"],
        "sla_due_field": due or "",
        "requisite_preset_org": next(p["ID"] for p in b.result("crm.requisite.preset.list") if p["NAME"] == "Организация"),
        "requisite_preset_ip": next(p["ID"] for p in b.result("crm.requisite.preset.list") if p["NAME"] == "ИП"),
    }
    for k, v in settings.items():
        print(f"INSERT INTO settings (key, value) VALUES ('{k}', '{v}') "
              f"ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();")


if __name__ == "__main__":
    main()
