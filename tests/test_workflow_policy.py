"""Reliability rules from the project brief, enforced on the exported workflows (workflows/*.json)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "workflows").glob("*.json"))}
ERROR_WF = "@name:[B24HUB] 99 error handler"
EXTERNAL = {"n8n-nodes-base.httpRequest", "n8n-nodes-base.telegram"}

# Webhooks without n8n-level auth must check the caller themselves (Bitrix24 can't send our header).
UNAUTHENTICATED_WEBHOOKS = {
    "04_b24_events.json": "auth[application_token] vs b24_app / outbound token",
    "05_app.json": "viewer AUTH_ID verified with app.info on the stored portal",
    "05_app_install.json": "install batch with the request AUTH_ID; events by their own access_token",
    "05a_robot_inn_check.json": "auth[application_token] vs b24_app",
    "05b_deal_tab.json": "viewer AUTH_ID verified with app.info on the stored portal",
    "05c_app_events.json": "auth[application_token] vs b24_app",
    "91_b24_mock.json": "test simulator, inactive by default",
}
# The only places allowed to talk to the portal directly; everything else goes through lib b24_call.
DIRECT_PORTAL_HTTP = {"lib_b24_call.json", "05_app.json", "05_app_install.json", "05b_deal_tab.json"}


def nodes(kind=None):
    for fname, wf in WORKFLOWS.items():
        for n in wf["nodes"]:
            if kind is None or n["type"] in (kind if isinstance(kind, set) else {kind}):
                yield fname, n


def test_there_are_workflows():
    assert len(WORKFLOWS) >= 10


@pytest.mark.parametrize("fname", sorted(WORKFLOWS))
def test_name_prefix(fname):
    assert WORKFLOWS[fname]["name"].startswith("[B24HUB] ")


@pytest.mark.parametrize("fname", sorted(set(WORKFLOWS) - {"99_error_handler.json", "91_b24_mock.json"}))
def test_error_workflow_is_set(fname):
    assert WORKFLOWS[fname]["settings"].get("errorWorkflow") == ERROR_WF


@pytest.mark.parametrize("fname, node", [(f, n) for f, n in nodes(EXTERNAL)], ids=lambda x: x if isinstance(x, str) else x["name"])
def test_external_calls_retry_and_do_not_swallow_errors(fname, node):
    assert node.get("retryOnFail") is True and node.get("maxTries", 3) >= 2, f"{fname}/{node['name']}: Retry On Fail"
    # Retry On Fail combined with On Error = continue makes n8n ignore Max Tries.
    assert node.get("onError") not in ("continueRegularOutput", "continueErrorOutput"), f"{fname}/{node['name']}"


def test_http_errors_are_inspected_not_thrown():
    """HTTP nodes return the full response with neverError, so Bitrix24 errors in a 200 body are seen."""
    for fname, n in nodes("n8n-nodes-base.httpRequest"):
        resp = n["parameters"].get("options", {}).get("response", {}).get("response", {})
        assert resp.get("neverError") and resp.get("fullResponse"), f"{fname}/{n['name']}"


def test_portal_is_called_only_through_the_wrapper():
    for fname, n in nodes("n8n-nodes-base.httpRequest"):
        url = n["parameters"]["url"]
        if "dadata.ru" in url or "oauth.bitrix24.tech" in url:
            continue
        assert fname in DIRECT_PORTAL_HTTP, f"{fname}/{n['name']} calls {url} directly"


def test_webhooks_are_authenticated():
    for fname, n in nodes("n8n-nodes-base.webhook"):
        if n["parameters"].get("authentication", "none") == "none":
            assert fname in UNAUTHENTICATED_WEBHOOKS, f"{fname}/{n['name']}: no auth and no own check"


def test_mock_is_inactive():
    assert WORKFLOWS["91_b24_mock.json"]["active"] is False


def test_libraries_are_called_one_item_at_a_time():
    for fname, n in nodes("n8n-nodes-base.executeWorkflow"):
        assert n["parameters"].get("mode") == "each", f"{fname}/{n['name']}"


def test_high_volume_success_executions_are_not_persisted():
    """Polling and wrapper workflows would flood the execution store (and lib carries the portal URL)."""
    for fname in ("02_process.json", "03_sla_watcher.json", "04b_deal_poller.json", "lib_b24_call.json", "lib_b24_app_token.json"):
        assert WORKFLOWS[fname]["settings"]["saveDataSuccessExecution"] == "none", fname


def test_exports_contain_no_secrets():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_exports.py")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
