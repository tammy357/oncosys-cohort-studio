"""Cohort Studio web app: an API over cohort_studio plus a single-page interface, and the hosted AI service.

Run from the repository root:
    pip install -r frontend/requirements.txt
    python frontend/server.py            # http://127.0.0.1:8000

The hosted AI service (for the CLI and scripts without their own key; cohort_studio/hosted.py is the client):
    POST /api/v1/draft   {"request": "..."} -> {"spec", "problems", "model", "limits"}
    GET  /api/v1/status  -> whether a model is configured, and the caller's limits today
    POST /mcp            the MCP server for AI agents (backend/mcp_http.py)
Send "Authorization: Bearer <token>" for a personal token's higher limit (tokens: COHORT_STUDIO_API_TOKENS in the
server's environment). Every AI request, from any page or client, passes the limits in backend/limits.py.
"""
import hmac
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import Body, FastAPI, Header, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from frontend.backend.canvas import Canvas  # noqa: E402
from frontend.backend.config import Settings  # noqa: E402
from frontend.backend.limits import Caller, RequestRate  # noqa: E402
from frontend.backend.mcp_http import add_mcp_routes  # noqa: E402
from frontend.backend.service import Service, ServiceError  # noqa: E402
from frontend.backend.storage import Storage  # noqa: E402

STATIC = pathlib.Path(__file__).resolve().parent / "static"


def _user(x_user):
    user = re.sub(r"[^A-Za-z0-9_.@-]", "", (x_user or "").strip())[:64]
    return user or "local"


def _ip(request):
    """The caller's address: the first X-Forwarded-For entry (Vercel and Caddy proxy every request), else the peer."""
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "")
    return re.sub(r"[^0-9A-Za-z.:-]", "", ip)[:64] or "unknown"


def _caller(settings, request, authorization=None, x_user=None, need_token=False):
    """Who is asking: a personal-token holder, or an address plus the browser's user id. A wrong token is refused."""
    token = (authorization or "").removeprefix("Bearer ").removeprefix("bearer ").strip()
    if token:
        name = next((n for t, n in settings.api_tokens.items()
                     if hmac.compare_digest(t.encode("utf-8"), token.encode("utf-8"))), None)
        if name is None:
            raise ServiceError("That API token isn't valid. Remove COHORT_STUDIO_API_TOKEN to use the shared limits, "
                               "or ask the team for a token.", 401)
        return Caller(identity=f"token:{name}", ip=_ip(request), token_name=name)
    if need_token:
        raise ServiceError("This service needs a personal token (COHORT_STUDIO_API_TOKEN). The built-in parser works "
                           "without one.", 401)
    return Caller(identity=f"anon:{_ip(request)}|{_user(x_user)}", ip=_ip(request))


def create_app(settings=None, client=None, storage=None):
    settings = settings or Settings()
    storage = storage or Storage(settings.database_url)
    svc = Service(settings, storage, client=client)
    app = FastAPI(title="Cohort Studio", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.service = svc
    rate = RequestRate(settings.requests_per_minute)

    @app.middleware("http")
    async def _protect_api(request: Request, call_next):
        # Basic protection for a small server: a size cap on request bodies and a per-address request rate.
        if (request.url.path.startswith("/api/") and request.url.path != "/api/health") or request.url.path == "/mcp":
            length = request.headers.get("content-length", "")
            if length.isdigit() and int(length) > settings.max_body_bytes:
                return JSONResponse({"error": "That request is too large."}, status_code=413)
            if not rate.allow(_ip(request)):
                return JSONResponse({"error": "Too many requests from this address; wait a minute and try again."},
                                    status_code=429)
        return await call_next(request)

    @app.middleware("http")
    async def _revalidate_assets(request: Request, call_next):
        # The page and its assets are small: always revalidate (ETag) so an update is never hidden by a stale cache.
        response = await call_next(request)
        if request.url.path in ("/", "/compare", "/canvas") or request.url.path.startswith("/static/"):
            response.headers["cache-control"] = "no-cache"
        return response

    @app.exception_handler(ServiceError)
    async def _service_error(request: Request, exc: ServiceError):
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/meta")
    def meta(x_user: str = Header(None)):
        return svc.meta()

    @app.get("/api/suggest")
    def suggest(q: str = "", x_user: str = Header(None)):
        return {"suggestions": svc.suggest(_user(x_user), q)}

    @app.post("/api/draft")
    def draft(request: Request, body: dict = Body(...), x_user: str = Header(None)):
        return svc.draft(_user(x_user), body.get("query", ""), use_ai=body.get("use_ai", True),
                         refine_search_id=body.get("refine_search_id"), current_steps=body.get("current_steps"),
                         current_request=body.get("current_request"), history=body.get("history"),
                         caller=_caller(settings, request, x_user=x_user))

    @app.post("/api/v1/draft")
    def v1_draft(request: Request, body: dict = Body(...), authorization: str = Header(None)):
        caller = _caller(settings, request, authorization, need_token=settings.require_token)
        return svc.api_draft(caller, body.get("request") or body.get("query") or "")

    @app.get("/api/v1/status")
    def v1_status(request: Request, authorization: str = Header(None)):
        return svc.api_status(_caller(settings, request, authorization, need_token=settings.require_token))

    # The MCP server for AI agents (cohort_studio/mcp_server.py over streamable HTTP), same callers and limits.
    add_mcp_routes(app, svc, lambda request, authorization: _caller(settings, request, authorization,
                                                                     need_token=settings.require_token))

    @app.post("/api/preview")
    def preview(body: dict = Body(...)):
        return svc.preview(body.get("steps") or [], body.get("base_steps"), impacts=body.get("impacts", True))

    @app.get("/api/searches/{search_id}/versions")
    def versions(search_id: int, x_user: str = Header(None)):
        return svc.versions(_user(x_user), search_id)

    @app.get("/api/searches/{search_id}/versions/{n}")
    def version(search_id: int, n: int, x_user: str = Header(None)):
        return svc.version(_user(x_user), search_id, n)

    @app.post("/api/searches/{search_id}/versions")
    def save_version(search_id: int, body: dict = Body(...), x_user: str = Header(None)):
        return svc.save_version(_user(x_user), search_id, body.get("steps") or [], body.get("note", ""), body.get("source", "edit"))

    @app.post("/api/fork")
    def fork(body: dict = Body(...), x_user: str = Header(None)):
        return svc.fork(_user(x_user), body.get("steps") or [], body.get("query"), body.get("base_search_id"), body.get("note", ""))

    @app.post("/api/advanced")
    def advanced(body: dict = Body(...), x_user: str = Header(None)):
        return svc.advanced(_user(x_user), body.get("form") or {})

    @app.post("/api/advanced/preview")
    def advanced_preview(body: dict = Body(...)):
        return svc.advanced_preview(body.get("form") or {})

    @app.get("/api/compare")
    def compare(a: str, b: str, x_user: str = Header(None)):
        return svc.compare(_user(x_user), a, b)

    @app.post("/api/compare/insights")
    def compare_insights(request: Request, body: dict = Body(...), x_user: str = Header(None)):
        return svc.compare_insights(_user(x_user), body.get("a"), body.get("b"),
                                    caller=_caller(settings, request, x_user=x_user))

    @app.post("/api/drafts/{draft_id}/resolve")
    def resolve(draft_id: str, body: dict = Body(...), x_user: str = Header(None)):
        return svc.resolve(_user(x_user), draft_id, body.get("answers") or {})

    @app.post("/api/run")
    def run(body: dict = Body(...), x_user: str = Header(None)):
        return svc.run(_user(x_user), body.get("spec"), body.get("query"), body.get("mode", "ai"),
                       draft_id=body.get("draft_id"), search_id=body.get("search_id"), edited=bool(body.get("edited")))

    @app.get("/api/searches")
    def searches(limit: int = 30, x_user: str = Header(None)):
        return {"searches": svc.history(_user(x_user), limit)}

    @app.get("/api/searches/{search_id}")
    def open_search(search_id: int, x_user: str = Header(None)):
        return svc.open_search(_user(x_user), search_id)

    @app.delete("/api/searches/{search_id}")
    def delete_search(search_id: int, x_user: str = Header(None)):
        return svc.delete_search(_user(x_user), search_id)

    @app.post("/api/searches/{search_id}/fix")
    def fix(search_id: int, body: dict = Body(default={}), x_user: str = Header(None)):
        return svc.fix(_user(x_user), search_id, body.get("step_index"))

    @app.post("/api/searches/{search_id}/confirm")
    def confirm(search_id: int, x_user: str = Header(None)):
        return svc.confirm_unparsed(_user(x_user), search_id)

    @app.get("/api/searches/{search_id}/files/{name}")
    def export(search_id: int, name: str, x_user: str = Header(None), user: str = None):
        content, media = svc.export(_user(x_user or user), search_id, name)
        return Response(content, media_type=media, headers={"content-disposition": f'attachment; filename="{name}"'})

    @app.get("/api/favorites")
    def favorites(x_user: str = Header(None)):
        return {"favorites": svc.favorites(_user(x_user))}

    @app.post("/api/favorites")
    def add_favorite(body: dict = Body(...), x_user: str = Header(None)):
        return svc.add_favorite(_user(x_user), body.get("query"), body.get("label"), body.get("search_id"))

    @app.delete("/api/favorites/{fav_id}")
    def delete_favorite(fav_id: int, x_user: str = Header(None)):
        return svc.delete_favorite(_user(x_user), fav_id)

    @app.post("/api/favorites/{fav_id}/run")
    def run_favorite(request: Request, fav_id: int, x_user: str = Header(None)):
        return svc.run_favorite(_user(x_user), fav_id, caller=_caller(settings, request, x_user=x_user))

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/compare")
    def compare_page():
        return FileResponse(STATIC / "compare.html")

    # ---------- the visual canvas (backend/canvas.py): the same steps, drawn as a graph ----------
    canvas = Canvas(svc)
    app.state.canvas = canvas

    @app.get("/canvas")
    def canvas_page():
        return FileResponse(STATIC / "canvas.html")

    @app.get("/api/canvas/catalog")
    def canvas_catalog():
        return canvas.catalog()

    @app.get("/api/canvas/genes")
    def canvas_genes(q: str = ""):
        return canvas.genes(q)

    @app.post("/api/canvas/evaluate")
    def canvas_evaluate(body: dict = Body(...)):
        return canvas.evaluate(body.get("steps") or [])

    @app.post("/api/canvas/code")
    def canvas_code(body: dict = Body(...)):
        return canvas.code(body.get("steps") or [], verify=bool(body.get("verify", True)))

    @app.get("/api/canvas/patients/{patient_id}")
    def canvas_patient(patient_id: str):
        return canvas.patient(patient_id)

    @app.post("/api/canvas/gallery")
    def canvas_gallery(body: dict = Body(...)):
        return canvas.gallery(body.get("steps") or [], body.get("limit", 24), body.get("kinds"))

    @app.get("/api/canvas/thumbs/{barcode}.jpg")
    def canvas_thumb(barcode: str):
        return Response(canvas.thumb(barcode), media_type="image/jpeg",
                        headers={"cache-control": "public, max-age=604800, immutable"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


if __name__ == "__main__":
    import os

    import uvicorn
    uvicorn.run(create_app(), host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 8000)))
