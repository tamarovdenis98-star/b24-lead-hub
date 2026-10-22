"""Idempotency and concurrency on a real Postgres with the project's migrations applied.

The SQL is taken from the exported workflow nodes, so the tests run the queries n8n runs.
Needs TEST_PG_DSN (a throwaway database: tables are truncated). CI provides one; locally e.g.
  TEST_PG_DSN=postgresql://b24hub_owner:...@127.0.0.1:55433/b24hub_test
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_PG_DSN not set")
ROOT = Path(__file__).resolve().parent.parent
TABLES = "inbox, intake_rejects, assignments, lead_events, client_locks, managers, routing_rules, b24_call_log, errors"


def node_sql(workflow_file: str, node_name: str) -> str:
    wf = json.loads((ROOT / "workflows" / workflow_file).read_text(encoding="utf-8"))
    q = next(n["parameters"]["query"] for n in wf["nodes"] if n["name"] == node_name)
    return re.sub(r"\$(\d+)", r"%(p\1)s", q)  # n8n $1 -> psycopg named params


def params(*values) -> dict:
    return {f"p{i}": v for i, v in enumerate(values, start=1)}


def connect(autocommit: bool = True):
    # n8n's Postgres node (pg-promise) interpolates parameters client-side; ClientCursor does the same,
    # so untyped parameters (e.g. inside jsonb_build_object) behave exactly as in production.
    return psycopg.connect(DSN, autocommit=autocommit, cursor_factory=psycopg.ClientCursor)


@pytest.fixture(autouse=True)
def clean():
    with connect() as c:
        c.execute(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE")
        c.execute("UPDATE rate_limiter SET next_slot = now() WHERE key = 'b24'")
    yield


INSERT_INBOX = node_sql("01_intake.json", "Insert inbox")


def intake(conn, key: str, body: dict | None = None):
    return conn.execute(INSERT_INBOX, params(key, "web", json.dumps(body or {"k": key}))).fetchone()


# ---------------------------------------------------------------- intake idempotency
def test_same_key_twice_is_one_row():
    with connect() as c:
        first = intake(c, "web:1")
        second = intake(c, "web:1", {"changed": True})
        assert first[0] is not None and second[0] is None   # inserted_id only the first time
        assert second[1] == first[0]                         # existing_id points to the same row
        assert c.execute("SELECT count(*), max(payload->>'k') FROM inbox").fetchone() == (1, "web:1")  # first payload kept


def test_parallel_identical_requests_create_one_row():
    key, results, errors = f"web:{uuid.uuid4()}", [], []

    def worker():
        try:
            with connect() as c:
                results.append(intake(c, key)[0])
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(30)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert sum(r is not None for r in results) == 1
    with connect() as c:
        assert c.execute("SELECT count(*) FROM inbox WHERE idempotency_key = %s", (key,)).fetchone()[0] == 1


# ---------------------------------------------------------------- workers: FOR UPDATE SKIP LOCKED
CLAIM = node_sql("02_process.json", "Claim batch")


def test_concurrent_workers_claim_disjoint_rows():
    with connect() as c:
        for i in range(15):
            intake(c, f"web:{i}")
    a, b = connect(autocommit=False), connect(autocommit=False)
    try:
        rows_a = a.execute(CLAIM).fetchall()       # 10 (batch size), transaction still open
        rows_b = b.execute(CLAIM).fetchall()       # skips A's locked rows
        ids_a, ids_b = {r[0] for r in rows_a}, {r[0] for r in rows_b}
        assert len(ids_a) == 10 and len(ids_b) == 5 and not ids_a & ids_b
        a.commit(); b.commit()
    finally:
        a.close(); b.close()
    with connect() as c:
        assert c.execute("SELECT count(*) FILTER (WHERE status = 'processing' AND attempt_count = 1) FROM inbox").fetchone()[0] == 15


def test_failed_lead_waits_for_backoff_and_stops_after_max_attempts():
    with connect() as c:
        intake(c, "web:f")
        mark_failed = node_sql("02_process.json", "Mark failed")
        for attempt in range(1, 6):
            assert len(c.execute(CLAIM).fetchall()) == 1
            c.execute(mark_failed, params(1, f"boom {attempt}"))
            assert c.execute(CLAIM).fetchall() == []                 # backoff: not picked again right away
            c.execute("UPDATE inbox SET next_attempt_at = now() WHERE id = 1")
        assert c.execute(CLAIM).fetchall() == []                     # 5 attempts spent: terminal
        assert c.execute("SELECT status, attempt_count FROM inbox").fetchone() == ("failed", 5)


# ---------------------------------------------------------------- routing
def seed_routing(c):
    c.execute("INSERT INTO managers (user_id, name) VALUES (16, 'm16'), (18, 'm18'), (20, 'm20')")
    c.execute("""INSERT INTO routing_rules (region, product, manager_ids, priority) VALUES
                 ('москва', '*', '{16,18}', 100), ('*', '*', '{16,18,20}', 1000)""")


def test_route_lead_is_idempotent_and_balances_load():
    with connect() as c:
        seed_routing(c)
        ids = [intake(c, f"web:r{i}")[0] for i in range(6)]
        first = [c.execute("SELECT * FROM route_lead(%s, 'москва', NULL, NULL)", (i,)).fetchone() for i in ids]
        again = [c.execute("SELECT * FROM route_lead(%s, 'казань', 'crm', 20)", (i,)).fetchone() for i in ids]
        assert first == again                                   # a retried lead keeps its manager and reason
        assert sorted(r[0] for r in first) == [16, 16, 16, 18, 18, 18]  # Moscow rule, least loaded alternates


def test_own_client_goes_to_own_manager():
    with connect() as c:
        seed_routing(c)
        inbox_id = intake(c, "web:own")[0]
        mgr, rule, reason = c.execute("SELECT * FROM route_lead(%s, 'москва', NULL, 20)", (inbox_id,)).fetchone()
        assert (mgr, rule) == (20, None) and reason.startswith("existing client")


# ---------------------------------------------------------------- per-client lock
LOCK = node_sql("02a_process_lead.json", "Lock client")


def lock(c, inbox_id, keys):
    return c.execute(LOCK, params(json.dumps({"inbox_id": inbox_id}), json.dumps(keys), inbox_id)).fetchone()[0]["locked"]


def test_second_lead_of_same_client_is_deferred():
    with connect() as c:
        a, b = intake(c, "web:la")[0], intake(c, "web:lb")[0]
        assert lock(c, a, ["phone:+79161234567", "email:a@b.ru"]) is True
        assert lock(c, b, ["phone:+79161234567"]) is False       # held by a
        assert lock(c, a, ["phone:+79161234567", "email:a@b.ru"]) is True  # re-entrant for its owner
        c.execute("DELETE FROM client_locks WHERE inbox_id = %s", (a,))   # released when a finishes
        assert lock(c, b, ["phone:+79161234567"]) is True


def test_stale_lock_expires():
    with connect() as c:
        a, b = intake(c, "web:sa")[0], intake(c, "web:sb")[0]
        lock(c, a, ["inn:7707083893"])
        c.execute("UPDATE client_locks SET locked_at = now() - interval '6 minutes'")
        assert lock(c, b, ["inn:7707083893"]) is True


# ---------------------------------------------------------------- throttle
def test_throttle_slots_are_500ms_apart():
    with connect() as c:
        slots = [c.execute("SELECT rate_limit_reserve('b24')").fetchone()[0] for _ in range(20)]
    gaps = [(b - a).total_seconds() for a, b in zip(slots, slots[1:])]
    assert all(abs(g - 0.5) < 1e-6 for g in gaps)


# ---------------------------------------------------------------- SLA closing / escalation
def make_assignment(c, key, due_offset):
    inbox_id = intake(c, key)[0]
    c.execute("INSERT INTO managers (user_id, name) VALUES (18, 'm') ON CONFLICT DO NOTHING")
    c.execute("""INSERT INTO assignments (inbox_id, manager_id, reason, deal_id, sla_item_id, crm_created_at, sla_due)
                 VALUES (%s, 18, 't', %s, %s, now(), now() + %s::interval)""", (inbox_id, 1000 + inbox_id, 2000 + inbox_id, due_offset))
    return inbox_id


def test_closing_an_sla_twice_changes_nothing():
    close = node_sql("lib_sla_close.json", "Close SLA")
    with connect() as c:
        i = make_assignment(c, "web:c", "1 hour")
        assert len(c.execute(close, params(i, "met", "PREPARATION", 5, "2026-10-22T10:00:00+03:00", "poller", "e1")).fetchall()) == 1
        assert c.execute(close, params(i, "breached", "EXECUTING", 9, "x", "ONCRMDEALUPDATE", "e2")).fetchall() == []
        assert c.execute("SELECT sla_status FROM assignments WHERE inbox_id = %s", (i,)).fetchone()[0] == "met"
        assert c.execute("SELECT count(*) FROM lead_events WHERE step = 'sla_closed'").fetchone()[0] == 1


def test_watcher_claim_cannot_escalate_twice_and_keeps_closed_status():
    claim = node_sql("03_sla_watcher.json", "Claim")
    breached = node_sql("03_sla_watcher.json", "Mark breached")
    with connect() as c:
        i = make_assignment(c, "web:w", "-5 minutes")
        ids = "{%d}" % i
        assert len(c.execute(claim, params(ids)).fetchall()) == 1
        assert c.execute(claim, params(ids)).fetchall() == []     # overlapping run: nothing to claim
        c.execute("UPDATE assignments SET sla_status = 'met', sla_closed_at = now() WHERE inbox_id = %s", (i,))  # answered meanwhile
        c.execute(breached, params(ids, "{}", "e3"))
        assert c.execute("SELECT sla_status FROM assignments WHERE inbox_id = %s", (i,)).fetchone()[0] == "met"
