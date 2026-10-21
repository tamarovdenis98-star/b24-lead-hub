#!/usr/bin/env python3
"""Fails if workflow exports contain anything that looks like a secret or instance-specific id.
Complements gitleaks in CI with project-specific rules (Bitrix24 webhook URLs, Telegram tokens)."""
import json
import re
import sys
from pathlib import Path

RULES = {
    "bitrix24 inbound webhook": re.compile(r"https?://[\w.-]+/rest/\d+/[A-Za-z0-9]{8,}"),
    "telegram bot token": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    "credential id": re.compile(r'"credentials":\s*\{[^}]*"id"'),
    "bearer/token literal": re.compile(r"(?i)(bearer\s+[A-Za-z0-9._-]{20,}|api[_-]?key\"\s*:\s*\"[^\"{=][^\"]{12,})"),
    "x-n8n-api-key value": re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"),
}

failed = False
for path in sorted(Path(__file__).resolve().parent.parent.glob("workflows/*.json")):
    text = path.read_text(encoding="utf-8")
    wf = json.loads(text)  # must be valid JSON
    if not wf.get("name", "").startswith("[B24HUB]"):
        failed = True
        print(f"{path.name}: workflow name must start with [B24HUB]")
    for name, rx in RULES.items():
        for m in rx.finditer(text):
            failed = True
            print(f"{path.name}: {name}: {m.group(0)[:40]}...")
print("exports clean" if not failed else "SECRETS FOUND")
sys.exit(1 if failed else 0)
