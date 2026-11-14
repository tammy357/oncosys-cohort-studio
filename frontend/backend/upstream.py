"""Ask the team's hosted backend first, and answer on this computer only if it can't.

A copy of the app run from a clone (`python frontend/server.py`) has the pages and the whole API, but usually no model
key, so no AI. With an upstream set, every /api/ request is sent to the hosted backend first, the same backend the
Vercel site uses, which has the AI and its limits. The backend can be given at more than one address (by default the
AWS server itself, then the Vercel site that relays /api/ to it), because some networks' web filters block the
server's own domain; the next address is tried when one can't be reached or a filter answers in its place. If the
backend can't be reached at any address, is restarting, or turns the request away (429, 502, 503, 504), this computer
answers instead, and an address that failed isn't tried again for RETRY_AFTER seconds, so a dead network doesn't make
every click wait.

Loops are impossible by construction: the hosted server is started without an upstream (only `python
frontend/server.py` sets one), and a request that has already been forwarded once is never forwarded again.
"""
import os
import time

import httpx
from fastapi import Request, Response

VIA = "x-cohort-studio-via"                     # set on forwarded requests: never forward those again
BACKEND = "x-cohort-studio-backend"             # on every /api/ answer: "hosted" or "local", for anyone checking
FORWARD_HEADERS = {"content-type", "accept", "x-user", "authorization", "user-agent"}
DROP_HEADERS = {"content-encoding", "content-length", "transfer-encoding", "connection", "keep-alive", "server", "date"}
FALLBACK_STATUS = {429, 502, 503, 504}
RETRY_AFTER = 30.0


class _Unreachable(Exception):
    """This address didn't get the request to the backend: try the next one."""


class Upstream:
    def __init__(self, base_urls, transport=None, timeout=None, log=print):
        if isinstance(base_urls, str):
            base_urls = base_urls.split(",")
        self.bases = [u.strip().rstrip("/") for u in base_urls if u.strip()]
        # A long read timeout, the same one the command line gives the hosted service: an AI draft with a second
        # opinion can take a while. Connecting must be quick, so an unreachable address costs seconds, not minutes.
        timeout = timeout or float(os.environ.get("COHORT_STUDIO_HOSTED_TIMEOUT", 150))
        self.clients = {b: httpx.AsyncClient(base_url=b, transport=transport, follow_redirects=False,
                                             timeout=httpx.Timeout(timeout, connect=4.0)) for b in self.bases}
        self.down_until = {b: 0.0 for b in self.bases}
        self.last_error = {b: "" for b in self.bases}
        self.log = log

    def wants(self, request: Request) -> bool:
        return request.url.path.startswith("/api/") and not request.headers.get(VIA)

    async def forward(self, request: Request):
        """The hosted backend's answer, or None to answer on this computer."""
        live = [b for b in self.bases if time.monotonic() >= self.down_until[b]]
        if not live:
            return None
        body = await request.body()
        headers = {k: v for k, v in request.headers.items() if k.lower() in FORWARD_HEADERS}
        headers[VIA] = "local-copy"
        params = list(request.query_params.multi_items())
        for base in live:
            try:
                r = await self._send(base, request.method, request.url.path, params, body, headers)
            except _Unreachable as exc:
                self._down(base, str(exc), more=base != live[-1])
                continue
            except httpx.HTTPError as exc:          # reached, but no answer in time: another address won't do better
                self._down(base, type(exc).__name__)
                return None
            if r.status_code in FALLBACK_STATUS:    # the backend itself is busy or restarting, whichever address
                self._down(base, f"HTTP {r.status_code}")
                return None
            if self.last_error[base]:
                self.log(f"  hosted backend reachable again at {base}: asking it first")
                self.last_error[base] = ""
            out = Response(r.content, status_code=r.status_code,
                           headers={k: v for k, v in r.headers.items() if k.lower() not in DROP_HEADERS})
            out.headers[BACKEND] = "hosted"
            return out
        return None

    async def _send(self, base, method, path, params, body, headers):
        try:
            r = await self.clients[base].request(method, path, params=params, content=body, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise _Unreachable(type(exc).__name__) from None
        # The API never answers with a web page: one means a web filter or a Wi-Fi login page answered in its place.
        if r.headers.get("content-type", "").lower().startswith("text/html"):
            raise _Unreachable(f"a web page (HTTP {r.status_code}), probably a network filter")
        return r

    def _down(self, base, why, more=False):
        self.down_until[base] = time.monotonic() + RETRY_AFTER
        self.last_error[base] = why
        then = "trying the next address" if more else f"answering on this computer for the next {int(RETRY_AFTER)} s"
        self.log(f"  hosted backend at {base} unavailable ({why}): {then}")


def add_upstream(app, upstream: Upstream):
    """Every /api/ request goes to the hosted backend first; this app's own routes answer when it can't."""
    @app.middleware("http")
    async def _hosted_first(request: Request, call_next):
        if not upstream.wants(request):
            return await call_next(request)
        hosted = await upstream.forward(request)
        if hosted is not None:
            return hosted
        local = await call_next(request)
        local.headers[BACKEND] = "local"
        return local
    app.state.upstream = upstream
