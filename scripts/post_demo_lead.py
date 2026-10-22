#!/usr/bin/env python3
"""Sends one demo lead to the intake; running it twice shows the idempotent answer (duplicate: true).

  python scripts/post_demo_lead.py [external_id]
Env: INTAKE_URL, INTAKE_SECRET.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

ext = sys.argv[1] if len(sys.argv) > 1 else "demo-video-1"
body = {
    "source": "web", "external_id": ext,
    "contact": {"name": "Мария Иванова", "phone": "8 (921) 700-12-34", "email": "Maria.Ivanova@Example.ru"},
    "company": {"inn": "7736207543"},
    "message": "Срочно нужна интеграция сайта с CRM, бюджет 450 тыс",
    "region": "Москва", "product": "integration", "utm": {"source": "demo"},
}
req = urllib.request.Request(os.environ["INTAKE_URL"], data=json.dumps(body, ensure_ascii=False).encode(), method="POST",
                             headers={"Content-Type": "application/json; charset=utf-8", "X-B24Hub-Secret": os.environ["INTAKE_SECRET"]})
t = time.perf_counter()
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        print(r.status, r.read().decode(), f"{(time.perf_counter() - t) * 1000:.0f} ms")
except urllib.error.HTTPError as e:
    print(e.code, e.read().decode())
