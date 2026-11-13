"""The hosted MCP server: cohort_studio/mcp_server.py over streamable HTTP at /mcp, for AI agents.

POST /mcp takes one JSON-RPC message (or a batch) and answers with JSON (202 for notifications); GET and DELETE
answer 405 because the server keeps no session and sends nothing unasked. Plain-English requests are drafted by the
service's model through Service.api_draft, so every limit and token rule of the hosted AI service applies; when the
model can't be used (no key, over a limit, an error) the built-in parser drafts and the result says why. Agents and
command-line clients send no Origin header; a request carrying one from anywhere but this app's own hosts, localhost or
Claude is refused (DNS-rebinding protection, which the MCP spec requires).
"""
import json
import re

from fastapi import Header, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from cohort_studio.mcp_server import CohortTools, MCPServer
from cohort_studio.parse import parse_request

from .service import ServiceError

# Exact hosts only: a wildcard on vercel.app or onrender.com would admit any project someone names like ours.
ALLOWED_ORIGIN = re.compile(r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$|"
                            r"^https://(oncosys-cohort-studio-static\.vercel\.app|oncosys-cohort-studio\.onrender\.com|"
                            r"claude\.ai|claude\.com)$")


def hosted_drafter(svc, caller):
    def draft(request):
        try:
            out = svc.api_draft(caller, request)
            if out["problems"]:
                raise ServiceError("the AI draft can't run: " + "; ".join(out["problems"]))
        except ServiceError as exc:
            spec = parse_request(request, svc.store)
            spec["notes"].append(f"No AI draft was used ({exc}), so the built-in parser drafted the steps.")
            return spec, {"used": "rules", "label": "built-in parser", "tried": [str(exc)]}
        return out["spec"], {"used": "hosted", "label": f"AI, Cohort Studio service ({out['model']})", "tried": [],
                             "limits": out["limits"]}
    return draft


def add_mcp_routes(app, svc, caller_for):
    """caller_for(request, authorization) -> Caller (raises ServiceError 401 for a bad token)."""

    @app.post("/mcp")
    async def mcp_post(request: Request, authorization: str = Header(None)):
        origin = request.headers.get("origin")
        if origin and not ALLOWED_ORIGIN.match(origin):
            return JSONResponse({"error": "Origin not allowed."}, status_code=403)
        try:
            payload = json.loads(await request.body() or b"null")
        except ValueError:
            return JSONResponse(MCPServer.error(None, -32700, "Parse error"), status_code=400)
        caller = caller_for(request, authorization)
        server = MCPServer(CohortTools(svc.store, drafter=hosted_drafter(svc, caller), catalog=svc.catalog))
        reply = await run_in_threadpool(server.handle_payload, payload)
        if reply is None:
            return Response(status_code=202)
        return JSONResponse(reply)

    @app.get("/mcp")
    @app.delete("/mcp")
    def mcp_no_stream():
        return Response(status_code=405, headers={"allow": "POST"})
