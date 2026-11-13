"""The hosted MCP server at /mcp: streamable HTTP, the service's model and limits, and the parser fallback."""
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["COHORT_STUDIO_NO_DOTENV"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from cohort_studio.assistant import ScriptedClient  # noqa: E402
from frontend.backend.config import Settings  # noqa: E402
from frontend.server import create_app  # noqa: E402
from tests.make_fixture import build  # noqa: E402

PT = {"op": "sample_type", "values": ["Primary Tumor"]}
SLIDES = {"op": "join", "table": "slides", "key": "sample_id", "mode": "rows", "slide_kinds": ["diagnostic"]}
REQUEST = "tumours with a diagnostic slide"
HEADERS = {"accept": "application/json, text/event-stream"}


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    return build(tmp_path_factory.mktemp("release"))


def app(tmp_path, data_dir, **limits):
    settings = Settings(data_dir=str(data_dir), database_url=f"sqlite:///{(tmp_path / 'app.db').as_posix()}", **limits)
    return TestClient(create_app(settings, client=ScriptedClient({REQUEST: {"steps": [PT, SLIDES]}})))


def rpc(c, method, params=None, headers=None):
    return c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                  headers={**HEADERS, **(headers or {})})


def test_streamable_http_basics(tmp_path, data_dir):
    c = app(tmp_path, data_dir)
    init = rpc(c, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
    assert init.status_code == 200 and init.json()["result"]["serverInfo"]["name"] == "cohort-studio"
    note = c.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=HEADERS)
    assert note.status_code == 202
    assert c.get("/mcp").status_code == 405
    assert rpc(c, "tools/list").json()["result"]["tools"]
    for origin in ("https://evil.example", "https://oncosys-cohort-studio-evil.vercel.app",
                   "https://oncosys-cohort-studio.onrender.com.evil.example"):
        assert rpc(c, "ping", headers={"origin": origin}).status_code == 403, origin
    for origin in ("http://localhost:6274", "https://oncosys-cohort-studio-static.vercel.app", "https://claude.ai"):
        assert rpc(c, "ping", headers={"origin": origin}).status_code == 200, origin
    assert c.post("/mcp", content=b"{not json", headers=HEADERS).json()["error"]["code"] == -32700


def test_agents_get_the_service_model_and_its_limits(tmp_path, data_dir):
    c = app(tmp_path, data_dir, ai_user_daily=1)
    first = rpc(c, "tools/call", {"name": "build_cohort", "arguments": {"request": REQUEST}}).json()["result"]
    out = first["structuredContent"]
    assert out["drafted_by"] == "AI, Cohort Studio service (scripted)" and out["inflation_fixed"]
    # over the daily limit: the agent still gets a cohort, drafted by the built-in parser, and is told why
    second = rpc(c, "tools/call", {"name": "build_cohort", "arguments": {"request": REQUEST}}).json()["result"]
    assert second["structuredContent"]["drafted_by"] == "built-in parser"
    assert any("today's 1 AI requests" in n for n in second["structuredContent"]["notes"])


def test_tokens_apply_to_agents_too(tmp_path, data_dir):
    token = "agent-" + "k" * 26
    c = app(tmp_path, data_dir, api_tokens={token: "agent"}, require_token=True)
    assert rpc(c, "ping").status_code == 401
    assert rpc(c, "ping", headers={"authorization": f"Bearer {token}"}).json()["result"] == {}
