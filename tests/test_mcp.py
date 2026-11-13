"""The MCP server for AI agents (cohort_studio/mcp_server.py): protocol, tools, and the stdio transport."""
import csv
import io
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cohort_studio.data import DataStore  # noqa: E402
from cohort_studio.llm import draft_spec  # noqa: E402
from cohort_studio.mcp_server import FILES, CohortTools, MCPServer, serve_stdio  # noqa: E402
from tests.make_fixture import build  # noqa: E402

PT = {"op": "sample_type", "values": ["Primary Tumor"]}
SLIDES = {"op": "join", "table": "slides", "key": "sample_id", "mode": "rows", "slide_kinds": ["diagnostic (FFPE)"]}


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    return build(tmp_path_factory.mktemp("release"))


@pytest.fixture(scope="module")
def server(data_dir):
    store = DataStore(data_dir)
    return MCPServer(CohortTools(store, drafter=lambda r: draft_spec(r, store, ai="off")))


def rpc(server, method, params=None, msg_id=1):
    return server.handle({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}})


def call(server, name, **arguments):
    return rpc(server, "tools/call", {"name": name, "arguments": arguments})["result"]


def test_handshake_and_tool_list(server):
    init = rpc(server, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                      "clientInfo": {"name": "test", "version": "0"}})["result"]
    assert init["protocolVersion"] == "2025-06-18" and "tools" in init["capabilities"]
    assert rpc(server, "initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == "2025-11-25"
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert rpc(server, "ping")["result"] == {}
    tools = rpc(server, "tools/list")["result"]["tools"]
    assert [t["name"] for t in tools] == ["describe_release", "draft_cohort", "build_cohort", "get_cohort_file"]
    assert all(t["inputSchema"]["type"] == "object" and t["description"] for t in tools)
    assert rpc(server, "resources/list")["error"]["code"] == -32601
    assert rpc(server, "tools/call", {"name": "nope"})["error"]["code"] == -32602
    assert server.handle({"id": 1, "method": "ping"})["error"]["code"] == -32600          # not JSON-RPC 2.0


def test_build_a_cohort_fix_the_inflation_and_export_it(server):
    res = call(server, "build_cohort", definition={"request": "pt with a dx slide", "steps": [PT, SLIDES]})
    out = res["structuredContent"]
    assert not res["isError"] and out["ok"] and out["inflated_before_fix"] and out["inflation_fixed"]
    assert len(out["sample_ids"]) == len(set(out["sample_ids"])) == out["n_samples"] > 0
    assert out["verdict"] and out["checks"] and out["definition"]["steps"]
    csv_file = call(server, "get_cohort_file", definition=out["definition"], file="cohort_samples.csv")
    rows = list(csv.reader(io.StringIO(csv_file["content"][0]["text"])))
    assert rows[0] == ["sample_id"] and sorted(r[0] for r in rows[1:]) == sorted(out["sample_ids"])
    script = call(server, "get_cohort_file", definition=out["definition"], file="reproduce_cohort.py")
    assert "def " in script["structuredContent"]["content"]
    for name in FILES:
        assert not call(server, "get_cohort_file", definition=out["definition"], file=name)["isError"], name


def test_plain_english_draft_and_build(server):
    draft = call(server, "draft_cohort", request="ER-positive primary tumours")["structuredContent"]
    assert draft["drafted_by"].startswith("built-in parser") and draft["steps_in_words"] and not draft["problems"]
    built = call(server, "build_cohort", request="ER-positive primary tumours")["structuredContent"]
    assert built["n_samples"] > 0 and built["definition"]["steps"] == draft["definition"]["steps"]


def test_release_description_and_field_values(server):
    out = call(server, "describe_release")["structuredContent"]
    assert "SAMPLE TYPES" in out["release"] and '"op":"where"' in out["step_format"]
    er = call(server, "describe_release", table="clinical", field="er_status")["structuredContent"]
    assert er["field"] == "er_status" and er["profile"]["values"]


def test_mistakes_come_back_as_messages_not_crashes(server):
    for name, args in (("build_cohort", {}), ("build_cohort", {"definition": {"steps": "nope"}}),
                       ("get_cohort_file", {"definition": {"steps": [PT]}, "file": "secrets.txt"}),
                       ("describe_release", {"field": "smoking"}), ("draft_cohort", {"request": "x" * 2001})):
        res = rpc(server, "tools/call", {"name": name, "arguments": args})["result"]
        assert res["isError"] and res["content"][0]["text"], (name, args)
    bad = call(server, "build_cohort", definition={"steps": [PT, {"op": "filter", "table": "clinical",
                                                                  "field": "smoking", "cmp": "in", "value": ["y"]}]})
    assert not bad["structuredContent"]["ok"] and bad["structuredContent"]["steps"][1]["error"]


def test_stdio_transport(server):
    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}),
             json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}), "not json",
             json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})]
    out = io.StringIO()
    serve_stdio(server, io.StringIO("\n".join(lines) + "\n"), out)
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r.get("id") for r in replies] == [1, None, 2] and replies[1]["error"]["code"] == -32700


def test_the_module_speaks_only_json_on_stdout(data_dir):
    """A real process, as an MCP client starts it: every stdout line must be a JSON-RPC message."""
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "build_cohort", "arguments": {"request": "primary tumours with a diagnostic slide"}}}]
    env = {**os.environ, "COHORT_STUDIO_HOSTED": "off", "COHORT_STUDIO_NO_DOTENV": "1"}
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_BASE_URL"):
        env.pop(k, None)
    proc = subprocess.run([sys.executable, "-m", "cohort_studio.mcp_server", "--data-dir", str(data_dir)],
                          input="\n".join(json.dumps(m) for m in msgs) + "\n", capture_output=True, text=True,
                          cwd=ROOT, env=env, timeout=120)
    replies = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [r["id"] for r in replies] == [1, 2]
    built = replies[1]["result"]["structuredContent"]
    assert built["n_samples"] > 0 and "built-in parser" in built["drafted_by"]
