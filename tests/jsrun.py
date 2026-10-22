"""Runs JavaScript that lives inside exported n8n Code nodes, so tests exercise exactly what is deployed.

    fns = load_functions("02a_process_lead.json", "Normalize", ["normalizePhone", "innValid"])
    fns.call("normalizePhone", ["8 (916) 123-45-67"], ["123"])   # -> list of results, one per argument tuple
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY_MARKER = "// ---- n8n entry ----"


def node_code(workflow_file: str, node_name: str) -> str:
    wf = json.loads((ROOT / "workflows" / workflow_file).read_text(encoding="utf-8"))
    for n in wf["nodes"]:
        if n["name"] == node_name:
            return n["parameters"]["jsCode"]
    raise KeyError(f"{workflow_file}: no node {node_name!r}")


class JsFunctions:
    def __init__(self, source: str, names: list[str]):
        self.source = source
        self.names = names

    def call(self, fn: str, *arg_tuples) -> list:
        """Calls fn once per argument tuple in a single node process."""
        assert fn in self.names, fn
        node = shutil.which("node")
        if not node:
            raise RuntimeError("node is required to run workflow JavaScript")
        script = (self.source + "\n;(() => {\n"
                  f"  const fns = {{ {', '.join(self.names)} }};\n"
                  "  const calls = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
                  f"  const out = calls.map((args) => fns[{json.dumps(fn)}](...args));\n"
                  "  process.stdout.write(JSON.stringify(out));\n"
                  "})();\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            res = subprocess.run([node, path], input=json.dumps([list(a) for a in arg_tuples]),
                                 capture_output=True, text=True, encoding="utf-8", timeout=30)
        finally:
            Path(path).unlink(missing_ok=True)
        if res.returncode != 0:
            raise RuntimeError(res.stderr)
        return json.loads(res.stdout)

    def one(self, fn: str, *args):
        return self.call(fn, args)[0]


def load_functions(workflow_file: str, node_name: str, names: list[str], cut_at: str = ENTRY_MARKER) -> JsFunctions:
    """Takes the node's code up to `cut_at` (the part with pure functions, before n8n-specific `$json` access)."""
    code = node_code(workflow_file, node_name)
    if cut_at not in code:
        raise AssertionError(f"{workflow_file}/{node_name}: marker {cut_at!r} not found")
    return JsFunctions(code.split(cut_at)[0], names)
