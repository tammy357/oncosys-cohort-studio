"""The hosted AI service (/api/v1) and the limits on every AI request. Scripted model, synthetic release, no network."""
import datetime
import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["COHORT_STUDIO_NO_DOTENV"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from cohort_studio import hosted, llm  # noqa: E402
from cohort_studio.assistant import ScriptedClient  # noqa: E402
from cohort_studio.data import DataStore  # noqa: E402
from frontend.backend.config import Settings, parse_tokens  # noqa: E402
from frontend.backend.limits import estimate_cost  # noqa: E402
from frontend.server import create_app  # noqa: E402
from tests.make_fixture import build  # noqa: E402

PT = {"op": "sample_type", "values": ["Primary Tumor"]}
SLIDES = {"op": "join", "table": "slides", "key": "sample_id", "mode": "rows", "slide_kinds": ["diagnostic"]}
REPLIES = {"tumours with a diagnostic slide": {"steps": [PT, SLIDES]}}
TOKEN = "t0k-" + "x" * 28


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    return build(tmp_path_factory.mktemp("release"))


def app(tmp_path, data_dir, **limits):
    settings = Settings(data_dir=str(data_dir), database_url=f"sqlite:///{(tmp_path / 'app.db').as_posix()}", **limits)
    return TestClient(create_app(settings, client=ScriptedClient(REPLIES)))


def draft(c, headers=None, request="tumours with a diagnostic slide"):
    return c.post("/api/v1/draft", json={"request": request}, headers=headers or {})


def test_the_service_drafts_a_runnable_spec_and_counts_the_request(tmp_path, data_dir):
    c = app(tmp_path, data_dir, ai_user_daily=5)
    r = draft(c)
    assert r.status_code == 200
    body = r.json()
    assert body["problems"] == [] and body["spec"]["steps"][0] == PT and body["model"] == "scripted"
    assert body["limits"]["tier"] == "anonymous" and body["limits"]["requests_left_today"] == 4
    day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    assert c.app.state.service.db.usage(day, "global")["calls"] == 1          # stored, so a restart can't reset it
    assert c.get("/api/v1/status").json()["ai"]["available"]
    assert draft(c, request="").status_code == 400 and draft(c, request="x" * 2001).status_code == 413


def test_personal_tokens(tmp_path, data_dir):
    c = app(tmp_path, data_dir, api_tokens={TOKEN: "alice"}, require_token=True)
    assert draft(c).status_code == 401
    assert draft(c, {"authorization": "Bearer wrong-token-wrong-token"}).status_code == 401
    from frontend.server import _caller
    from frontend.backend.service import ServiceError
    from starlette.requests import Request
    raw = Request({"type": "http", "headers": [], "client": ("1.2.3.4", 1)})
    with pytest.raises(ServiceError) as exc:                  # a non-ASCII token is refused (401), not a crash
        _caller(c.app.state.service.settings, raw, "Bearer tök-ünïcode-tök-ünïcode")
    assert exc.value.status == 401
    ok = draft(c, {"authorization": f"Bearer {TOKEN}"})
    assert ok.status_code == 200 and ok.json()["limits"]["tier"] == "token"
    assert parse_tokens(f"alice:{TOKEN}, short:abc") == {TOKEN: "alice"}          # short tokens are refused
    assert TOKEN not in repr(c.app.state.service.settings)


def test_daily_and_per_minute_limits(tmp_path, data_dir):
    c = app(tmp_path, data_dir, ai_user_daily=2)
    assert [draft(c).status_code for _ in range(3)] == [200, 200, 429]
    assert "today's 2 AI requests" in draft(c).json()["error"]
    # the web app keeps working over the limit: the built-in parser drafts and says why
    web = c.post("/api/draft", json={"query": "ER-positive primary tumours"}).json()
    assert web["mode"] == "rules" and any("AI requests" in w for w in web["warnings"])
    m = app(tmp_path / "m", data_dir, ai_per_minute=1)
    assert [draft(m).status_code for _ in range(2)] == [200, 429]


def test_the_daily_budget_is_a_hard_stop(tmp_path, data_dir):
    c = app(tmp_path, data_dir, ai_daily_budget_usd=0)
    r = draft(c)
    assert r.status_code == 429 and "budget" in r.json()["error"]
    assert round(estimate_cost("gpt-4.1-mini", {"input": 10_000, "cached_input": 2_000, "output": 1_000}), 5) == 0.0050
    assert estimate_cost("some-new-model", {"input": 1_000_000}) >= 3            # unknown models are priced high


def test_request_rate_and_body_size(tmp_path, data_dir):
    c = app(tmp_path, data_dir, requests_per_minute=3, max_body_bytes=500)
    assert [c.get("/api/meta").status_code for _ in range(4)][-1] == 429
    big = app(tmp_path / "b", data_dir, max_body_bytes=500)
    assert big.post("/api/v1/draft", json={"request": "y" * 1000}).status_code == 413


def test_a_clone_without_a_key_gets_ai_through_the_service(tmp_path, data_dir, monkeypatch):
    """The whole route: the local waterfall finds no key, calls the service (here in-process), runs the spec locally."""
    c = app(tmp_path, data_dir)
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_BASE_URL", "COHORT_STUDIO_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("COHORT_STUDIO_HOSTED", "on")

    def via_test_client(method, path, payload=None, timeout=None):
        r = c.request(method, path, json=payload)
        if r.status_code != 200:
            raise hosted.HostedUnavailable(r.json().get("error", ""))
        return json.loads(r.text)
    monkeypatch.setattr(hosted, "_call", via_test_client)
    spec, route = llm.draft_spec("tumours with a diagnostic slide", DataStore(data_dir))
    assert route["used"] == "hosted" and spec["steps"][0] == PT
