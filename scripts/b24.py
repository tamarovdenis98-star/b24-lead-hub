"""Minimal Bitrix24 REST client for maintenance scripts (setup, cleanup, replay checks).

Workflows never use this: they go through [B24HUB] lib b24_call. The same rules apply here:
<=2 req/s, errors read from the body, backoff on QUERY_LIMIT_EXCEEDED / OPERATION_TIME_LIMIT.
Env: B24_WEBHOOK (inbound webhook URL, trailing slash optional).
"""
from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request

RETRYABLE = {"QUERY_LIMIT_EXCEEDED", "OPERATION_TIME_LIMIT", "INTERNAL_SERVER_ERROR"}


class B24Error(Exception):
    def __init__(self, method: str, code: str, description: str, status: int):
        super().__init__(f"{method}: {code} {description} (HTTP {status})")
        self.code, self.description, self.status = code, description, status


class B24:
    def __init__(self, webhook: str | None = None, rps: float = 2.0):
        self.base = (webhook or os.environ["B24_WEBHOOK"]).rstrip("/") + "/"
        self.min_gap = 1.0 / rps
        self._last = 0.0
        self.calls = 0
        self.retries = 0

    def call(self, method: str, params: dict | None = None, max_attempts: int = 6):
        for attempt in range(1, max_attempts + 1):
            wait = self._last + self.min_gap - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self.calls += 1
            req = urllib.request.Request(self.base + method + ".json", data=json.dumps(params or {}).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    status, raw = r.status, r.read()
            except urllib.error.HTTPError as e:
                status, raw = e.code, e.read()
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                body = {"error": "NON_JSON_RESPONSE", "error_description": raw[:200].decode(errors="replace")}
            code = body.get("error") or (f"HTTP_{status}" if status >= 400 else None)
            if not code:
                return body
            if (code in RETRYABLE or status in (429, 500, 502, 503, 504)) and attempt < max_attempts:
                self.retries += 1
                time.sleep(min(30, 2 ** (attempt - 1)) + random.random() / 4)
                continue
            raise B24Error(method, code, str(body.get("error_description", "")), status)

    def result(self, method: str, params: dict | None = None):
        return self.call(method, params)["result"]

    def list_all(self, method: str, params: dict | None = None, key: str | None = None):
        """Iterates a paged list method (50 per page). `key` unwraps e.g. result.items / result.tasks."""
        params = dict(params or {})
        start = 0
        while True:
            params["start"] = start
            body = self.call(method, params)
            page = body["result"][key] if key else body["result"]
            yield from page
            if "next" not in body:
                return
            start = body["next"]

    def batch(self, cmd: dict[str, str], halt: bool = False) -> dict:
        """Runs up to 50 commands in one request; raises if any sub-command failed."""
        body = self.call("batch", {"halt": 1 if halt else 0, "cmd": cmd})
        res = body["result"]
        errors = {k: v for k, v in (res.get("result_error") or {}).items() if v}
        if errors:
            first = next(iter(errors.items()))
            raise B24Error(f"batch[{first[0]}]", str(first[1].get("error", "")), str(first[1].get("error_description", "")), 200)
        return res
