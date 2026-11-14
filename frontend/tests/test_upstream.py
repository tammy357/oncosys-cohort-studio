"""Hosted backend first, this computer second (frontend/backend/upstream.py), and the command line's hosted client
trying the relay when the service's own address is blocked (cohort_studio/hosted.py). Fakes only: no network."""
import io
import json
import os
import pathlib
import sys
import urllib.error

import httpx
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["COHORT_STUDIO_NO_DOTENV"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from cohort_studio import hosted  # noqa: E402
from frontend.backend import upstream as up  # noqa: E402
from frontend.backend.config import Settings  # noqa: E402
from frontend.server import create_app  # noqa: E402
from tests.make_fixture import build  # noqa: E402

HOSTED, RELAY = "https://hosted.example", "https://relay.example"
BLOCK_PAGE = "<html><title>Web Page Blocked</title></html>"


class FakeHosted:
    """Records what reached each address and answers with `reply[host]` (default: a JSON answer)."""
    def __init__(self):
        self.seen = []
        self.reply = {}

    def __call__(self, request: httpx.Request):
        self.seen.append(request)
        fn = self.reply.get(request.url.host)
        return fn(request) if fn else httpx.Response(200, json={"from": request.url.host},
                                                     headers={"cache-control": "max-age=60"})

    def hosts(self):
        return [r.url.host for r in self.seen]


def refused(req):
    raise httpx.ConnectError("[WinError 10054] connection reset", request=req)


@pytest.fixture()
def make(tmp_path):
    data = build(tmp_path / "release")

    def _make(fake, upstream_url=f"{HOSTED},{RELAY}"):
        settings = Settings(data_dir=str(data), database_url=f"sqlite:///{(tmp_path / 'app.db').as_posix()}",
                            ai_enabled=False, upstream_url=upstream_url)
        app = create_app(settings, upstream_transport=httpx.MockTransport(fake))
        if settings.upstream_url:
            app.state.upstream.log = lambda msg: None
        return TestClient(app)
    return _make


def test_api_calls_go_to_the_hosted_backend_first(make):
    fake = FakeHosted()
    c = make(fake)
    r = c.post("/api/preview?x=1&x=2", json={"text": "primary tumours"}, headers={"x-user": "judge", "cookie": "a=b"})
    assert r.json() == {"from": "hosted.example"} and r.headers[up.BACKEND] == "hosted"
    assert r.headers["cache-control"] == "max-age=60"
    sent = fake.seen[0]
    assert fake.hosts() == ["hosted.example"]
    assert sent.method == "POST" and str(sent.url) == f"{HOSTED}/api/preview?x=1&x=2"
    assert json.loads(sent.content) == {"text": "primary tumours"}
    assert sent.headers["x-user"] == "judge" and sent.headers[up.VIA] and "cookie" not in sent.headers


def test_hosted_errors_that_are_answers_are_passed_on(make):
    fake = FakeHosted()
    fake.reply["hosted.example"] = lambda req: httpx.Response(404, json={"detail": "not found on hosted"})
    r = make(fake).get("/api/searches/nope")
    assert r.status_code == 404 and r.json()["detail"] == "not found on hosted" and r.headers[up.BACKEND] == "hosted"
    assert fake.hosts() == ["hosted.example"]


@pytest.mark.parametrize("blocked", [refused, lambda req: httpx.Response(503, html=BLOCK_PAGE),
                                     lambda req: httpx.Response(200, html=BLOCK_PAGE)])
def test_a_blocked_address_gives_way_to_the_relay(make, blocked):
    fake = FakeHosted()
    fake.reply["hosted.example"] = blocked
    c = make(fake)
    r = c.post("/api/preview", json={"text": "x"})
    assert r.json() == {"from": "relay.example"} and r.headers[up.BACKEND] == "hosted"
    assert fake.hosts() == ["hosted.example", "relay.example"] and json.loads(fake.seen[1].content) == {"text": "x"}
    c.get("/api/meta")                               # the blocked address isn't tried again for a while
    assert fake.hosts()[2:] == ["relay.example"]


def test_nothing_reachable_falls_back_here_and_stays_here_for_a_while(make):
    fake = FakeHosted()
    fake.reply = {"hosted.example": refused, "relay.example": refused}
    c = make(fake)
    r = c.get("/api/meta")
    assert r.status_code == 200 and r.headers[up.BACKEND] == "local" and "ai" in r.json()
    c.get("/api/meta")
    assert len(fake.seen) == 2                       # the second call didn't wait on dead addresses
    u = c.app.state.upstream
    u.down_until = {b: 0.0 for b in u.bases}         # the wait is over: the hosted backend is asked again
    fake.reply = {}
    assert c.get("/api/meta").headers[up.BACKEND] == "hosted" and fake.hosts()[-1] == "hosted.example"


@pytest.mark.parametrize("status", sorted(up.FALLBACK_STATUS))
def test_busy_or_restarting_backend_falls_back_here_with_the_body_intact(make, status):
    fake = FakeHosted()
    fake.reply["hosted.example"] = lambda req: httpx.Response(status, text="busy")
    c = make(fake)
    r = c.post("/api/preview", json={"spec": {"sample_types": ["Primary Tumor"]}})
    assert r.headers[up.BACKEND] == "local" and r.status_code < 500
    assert fake.hosts() == ["hosted.example"]        # the relay leads to the same busy backend: not asked
    assert json.loads(fake.seen[0].content) == {"spec": {"sample_types": ["Primary Tumor"]}}


def test_a_slow_backend_is_not_asked_twice(make):
    def slow(req):
        raise httpx.ReadTimeout("no answer in time", request=req)
    fake = FakeHosted()
    fake.reply["hosted.example"] = slow
    r = make(fake).get("/api/meta")
    assert r.headers[up.BACKEND] == "local" and fake.hosts() == ["hosted.example"]


def test_pages_and_forwarded_requests_stay_local(make):
    fake = FakeHosted()
    c = make(fake)
    assert c.get("/").status_code == 200 and c.get("/canvas").status_code == 200
    r = c.get("/api/meta", headers={up.VIA: "local-copy"})
    assert r.status_code == 200 and up.BACKEND not in r.headers
    assert fake.seen == []


def test_no_upstream_unless_asked_for(make, monkeypatch):
    monkeypatch.delenv("COHORT_STUDIO_UPSTREAM", raising=False)
    assert Settings().upstream_url == ""                        # the hosted server is started like this
    for off in ("", "off", "OFF", "none", "local", "false", "0"):
        assert Settings(upstream_url=off).upstream_url == ""
    assert Settings(upstream_url=" https://a.example/ , https://b.example/").upstream_url == \
        "https://a.example,https://b.example"
    fake = FakeHosted()
    c = make(fake, upstream_url="off")
    assert not hasattr(c.app.state, "upstream")
    r = c.get("/api/meta")
    assert r.status_code == 200 and up.BACKEND not in r.headers and fake.seen == []


# ---------- the command line's hosted client ----------

class FakeReply(io.BytesIO):
    def __init__(self, body, content_type="application/json"):
        super().__init__(body.encode())
        self.headers = {"content-type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_urlopen(answers, seen):
    def urlopen(req, timeout=None):
        seen.append(req.full_url)
        answer = answers[req.full_url.split("/api/")[0]]
        if isinstance(answer, BaseException):
            raise answer
        return answer
    return urlopen


@pytest.fixture()
def team_service(monkeypatch):
    monkeypatch.delenv("COHORT_STUDIO_API_URL", raising=False)
    assert hosted.base_urls() == [hosted.DEFAULT_URL, hosted.RELAY_URL]


@pytest.mark.parametrize("blocked", [
    urllib.error.URLError(ConnectionResetError(10054, "connection reset")),
    urllib.error.URLError(TimeoutError("connect timed out")),
    urllib.error.HTTPError(hosted.DEFAULT_URL, 503, "Service Unavailable", {"content-type": "text/html"}, io.BytesIO(b"")),
    FakeReply(BLOCK_PAGE, "text/html; charset=UTF-8"),
])
def test_the_command_line_tries_the_relay_when_the_service_is_blocked(monkeypatch, team_service, blocked):
    seen = []
    answers = {hosted.DEFAULT_URL: blocked, hosted.RELAY_URL: FakeReply('{"ai": {"available": true}}')}
    monkeypatch.setattr(hosted.urllib.request, "urlopen", fake_urlopen(answers, seen))
    assert hosted.status() == {"ai": {"available": True}}
    assert seen == [f"{hosted.DEFAULT_URL}/api/v1/status", f"{hosted.RELAY_URL}/api/v1/status"]


def test_the_command_line_gives_up_cleanly_when_nothing_answers(monkeypatch, team_service):
    seen = []
    down = urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
    monkeypatch.setattr(hosted.urllib.request, "urlopen", fake_urlopen({hosted.DEFAULT_URL: down, hosted.RELAY_URL: down}, seen))
    with pytest.raises(hosted.HostedUnavailable, match="can't be reached"):
        hosted.status()
    assert len(seen) == 2


@pytest.mark.parametrize("answer", [
    urllib.error.HTTPError(hosted.DEFAULT_URL, 429, "Too Many Requests", {"content-type": "application/json"},
                           io.BytesIO(b'{"error": "daily limit reached"}')),
    TimeoutError("read timed out"),
])
def test_the_command_line_does_not_ask_the_relay_when_the_service_answered_or_is_slow(monkeypatch, team_service, answer):
    seen = []
    monkeypatch.setattr(hosted.urllib.request, "urlopen", fake_urlopen({hosted.DEFAULT_URL: answer}, seen))
    with pytest.raises(hosted.HostedUnavailable):
        hosted.status()
    assert len(seen) == 1


def test_your_own_service_has_no_relay(monkeypatch):
    monkeypatch.setenv("COHORT_STUDIO_API_URL", "https://mine.example/")
    assert hosted.base_urls() == ["https://mine.example"]
