#!/usr/bin/env python3
"""Deploy / export n8n workflows of this project through the n8n Public API.

Workflow files in workflows/*.json are environment-independent:
  * credentials are referenced by name only (ids are resolved on deploy, dropped on export);
  * settings.errorWorkflow and Execute Workflow targets are stored as "@name:<workflow name>".
So the same file deploys to any n8n instance, and an export never carries instance ids or secrets.

Usage:
  python scripts/n8n_sync.py deploy [workflows/xx.json ...]   # upsert by name, tag, activate
  python scripts/n8n_sync.py export                             # pull [PREFIX] workflows into workflows/
  python scripts/n8n_sync.py credentials                        # create/update credentials from env
Env: N8N_URL, N8N_API_KEY (+ values listed in CREDENTIALS for `credentials`).
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

PREFIX = "[B24HUB]"
TAGS = ["b24hub", "portfolio"]
ROOT = Path(__file__).resolve().parent.parent
WF_DIR = ROOT / "workflows"
NAME_REF = "@name:"

# name -> (type, builder of data from env)
CREDENTIALS = {
    "B24HUB Postgres": ("postgres", lambda e: {
        "host": e.get("PG_HOST", "b24hub-postgres"), "port": int(e.get("PG_PORT", "5432")),
        "database": e.get("POSTGRES_DB", "b24hub"), "user": e.get("APP_DB_USER", "b24hub_app"),
        "password": e["APP_DB_PASSWORD"], "ssl": "disable", "maxConnections": 10,
        "allowUnauthorizedCerts": False, "sshTunnel": False,
    }),
    "B24HUB Intake Secret": ("httpHeaderAuth", lambda e: {
        "name": "X-B24Hub-Secret", "value": e["INTAKE_SECRET"],
    }),
    "B24HUB Telegram": ("telegramApi", lambda e: {
        "accessToken": e["TG_BOT_TOKEN"], "baseUrl": "https://api.telegram.org",
    }),
    "B24HUB DaData": ("httpHeaderAuth", lambda e: {
        "name": "Authorization", "value": "Token " + e["DADATA_API_KEY"],
    }),
}

ALLOWED_SETTINGS = {
    "saveExecutionProgress", "saveManualExecutions", "saveDataErrorExecution",
    "saveDataSuccessExecution", "executionTimeout", "errorWorkflow", "timezone",
    "executionOrder", "callerPolicy", "callerIds",
}


class Api:
    def __init__(self) -> None:
        self.base = os.environ["N8N_URL"].rstrip("/") + "/api/v1"
        self.key = os.environ["N8N_API_KEY"]

    def call(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "X-N8N-API-KEY": self.key, "Content-Type": "application/json", "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise SystemExit(f"{method} {path} -> {e.code}: {e.read().decode(errors='replace')[:500]}")

    def paged(self, path: str):
        cursor, sep = None, "&" if "?" in path else "?"
        while True:
            page = self.call("GET", path + sep + "limit=250" + (f"&cursor={cursor}" if cursor else ""))
            yield from page["data"]
            cursor = page.get("nextCursor")
            if not cursor:
                return


def walk_refs(obj, fn):
    """Apply fn to every string value in a nested structure."""
    if isinstance(obj, dict):
        return {k: walk_refs(v, fn) for k, v in obj.items()}
    if isinstance(obj, list):
        return [walk_refs(v, fn) for v in obj]
    if isinstance(obj, str):
        return fn(obj)
    return obj


def project_workflows(api: Api) -> dict[str, dict]:
    return {w["name"]: w for w in api.paged("/workflows") if w["name"].startswith(PREFIX)}


def ensure_tags(api: Api) -> list[dict]:
    existing = {t["name"]: t for t in api.paged("/tags")}
    out = []
    for name in TAGS:
        out.append(existing.get(name) or api.call("POST", "/tags", {"name": name}))
    return [{"id": t["id"]} for t in out]


def cmd_credentials() -> None:
    api = Api()
    existing = {c["name"]: c for c in api.paged("/credentials")}
    for name, (ctype, build) in CREDENTIALS.items():
        try:
            data = build(os.environ)
        except KeyError as e:
            print(f"skip  {name}: env {e} not set")
            continue
        body = {"name": name, "type": ctype, "data": data}
        if name in existing:
            api.call("PATCH", f"/credentials/{existing[name]['id']}", body)
            print(f"update {name}")
        else:
            api.call("POST", "/credentials", body)
            print(f"create {name}")


def cmd_deploy(files: list[str]) -> None:
    api = Api()
    paths = [Path(f) for f in files] or sorted(WF_DIR.glob("*.json"))
    # Libraries and the error handler first: others reference them by name.
    paths.sort(key=lambda p: (0 if re.search(r"(lib|99)", p.name) else 1, p.name))
    creds = {c["name"]: c for c in api.paged("/credentials")}
    tags = ensure_tags(api)
    current = project_workflows(api)

    for path in paths:
        wf = json.loads(path.read_text(encoding="utf-8"))
        name = wf["name"]
        assert name.startswith(PREFIX), f"{path}: name must start with {PREFIX}"
        current = project_workflows(api)

        def resolve(s: str) -> str:
            if s.startswith(NAME_REF):
                target = s[len(NAME_REF):]
                if target == name and name not in current:
                    return s  # self-reference on first create, fixed in second pass
                if target not in current:
                    raise SystemExit(f"{path}: referenced workflow '{target}' is not deployed yet")
                return current[target]["id"]
            return s

        nodes = walk_refs(wf["nodes"], resolve)
        for node in nodes:
            for ctype, ref in (node.get("credentials") or {}).items():
                c = creds.get(ref["name"])
                if not c:
                    raise SystemExit(f"{path}: credential '{ref['name']}' not found in n8n")
                node["credentials"][ctype] = {"id": c["id"], "name": c["name"]}
        settings = {k: v for k, v in walk_refs(wf.get("settings", {}), resolve).items() if k in ALLOWED_SETTINGS}
        body = {"name": name, "nodes": nodes, "connections": wf["connections"], "settings": settings}

        if name in current:
            wid = current[name]["id"]
            if current[name].get("active"):
                api.call("POST", f"/workflows/{wid}/deactivate")
            api.call("PUT", f"/workflows/{wid}", body)
            action = "update"
        else:
            wid = api.call("POST", "/workflows", body)["id"]
            action = "create"
        if NAME_REF in json.dumps(body):  # self-reference (e.g. error handler) now resolvable
            current = project_workflows(api)
            body = json.loads(json.dumps(body).replace(NAME_REF + name, wid))
            api.call("PUT", f"/workflows/{wid}", body)
        api.call("PUT", f"/workflows/{wid}/tags", tags)
        if wf.get("active"):
            api.call("POST", f"/workflows/{wid}/activate")
        print(f"{action} {name} id={wid} active={bool(wf.get('active'))}")


def slug(name: str) -> str:
    s = name[len(PREFIX):].strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def cmd_export() -> None:
    api = Api()
    wfs = project_workflows(api)
    by_id = {w["id"]: n for n, w in wfs.items()}
    WF_DIR.mkdir(exist_ok=True)
    for name, meta in sorted(wfs.items()):
        wf = api.call("GET", f"/workflows/{meta['id']}")

        def to_name(s: str) -> str:
            return NAME_REF + by_id[s] if s in by_id else s

        nodes = []
        for node in wf["nodes"]:
            node = dict(node)
            if node.get("credentials"):
                node["credentials"] = {t: {"name": c["name"]} for t, c in node["credentials"].items()}
            nodes.append(node)
        nodes = walk_refs(nodes, to_name)
        settings = walk_refs({k: v for k, v in (wf.get("settings") or {}).items() if k in ALLOWED_SETTINGS}, to_name)
        out = {
            "name": name, "active": wf.get("active", False),
            "nodes": sorted(nodes, key=lambda n: n["name"]),
            "connections": wf["connections"], "settings": settings,
        }
        path = WF_DIR / f"{slug(name)}.json"
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8", newline="\n")
        print(f"export {name} -> {path.relative_to(ROOT)}")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in {"deploy", "export", "credentials"}:
        raise SystemExit(__doc__)
    {"deploy": lambda: cmd_deploy(sys.argv[2:]), "export": cmd_export, "credentials": cmd_credentials}[sys.argv[1]]()
