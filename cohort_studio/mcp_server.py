"""An MCP server, so AI agents (Claude, Cursor, any MCP client) can build TCGA-BRCA cohorts with Cohort Studio.

Two ways to connect:
  hosted, nothing to install: add https://api.maplestreettax.site/mcp as a remote MCP server
      (streamable HTTP; optional "Authorization: Bearer <token>" for a personal token's higher limit). Plain-English
      requests are drafted by the service's model within its daily limits, else by the built-in parser.
  local, from a clone (runs on your copy of the data):   python -m cohort_studio.mcp_server
      stdio transport, e.g.  claude mcp add cohort-studio -- python -m cohort_studio.mcp_server
      Requests are drafted by your own key, else the hosted service, else the built-in parser (cohort_studio/llm.py);
      --rules uses only the built-in parser, --no-hosted never calls the hosted service.

Tools
  describe_release  what the frozen release holds (sample types, layers, slide kinds, fields and their values) and
                    the step format, so an agent can write or edit a cohort definition itself
  draft_cohort      plain English -> an editable cohort definition (steps in JSON and in words), not run
  build_cohort      a request or a definition -> run, fix any inflating join, verdict, sample IDs, definition
  get_cohort_file   a definition -> one export, e.g. cohort_samples.csv or reproduce_cohort.py

The model only ever drafts steps; every number comes from the engine on the frozen files. The protocol layer
(JSON-RPC 2.0: initialize, ping, tools/list, tools/call) is implemented here, so no extra package is needed.
"""
import argparse
import io
import json
import sys

from . import exports
from .api import build_cohort
from .data import DataStore
from .llm import draft_spec
from .spec import describe_step, validate

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "cohort-studio", "title": "Cohort Studio (TCGA-BRCA cohorts)", "version": "1.0.0"}
INSTRUCTIONS = (
    "Cohort Studio builds TCGA-BRCA cohorts from the OncoSys event's frozen release. Typical use: build_cohort with "
    "a plain-English request; check its steps, verdict and notes (the model only drafts steps, the engine computes "
    "every count); to change something, edit the returned 'definition' (see describe_release for the step format) "
    "and call build_cohort with it; then get_cohort_file for cohort_samples.csv (one row per sample, sample_id = "
    "15-character TCGA barcode) and reproduce_cohort.py. Joins that count a sample twice are fixed automatically and "
    "reported. Research use only: no diagnosis, treatment recommendation or clinical claim.")
FILES = ("cohort_samples.csv", "reproduce_cohort.py", "cohort_spec.json", "ledger.csv", "exclusions.csv",
         "sample_selection.csv", "coverage.csv", "verdict.json", "findings.json")
MAX_REQUEST_CHARS = 2000

_DEFINITION = {"type": "object", "description": (
    "A cohort definition to run exactly as written: {\"request\": \"...\", \"steps\": [...]}, e.g. the 'definition' "
    "returned by draft_cohort or build_cohort, edited if needed. describe_release explains the step format."),
    "properties": {"request": {"type": "string"}, "steps": {"type": "array", "items": {"type": "object"}}},
    "required": ["steps"]}
_REQUEST = {"type": "string", "maxLength": MAX_REQUEST_CHARS, "description": (
    "The cohort in plain English, e.g. 'primary tumors with both RNA and protein data and a diagnostic slide' or "
    "'ER-negative or TP53-mutated primary tumours, one sample per patient'.")}
_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
TOOLS = [
    {"name": "describe_release", "title": "Describe the TCGA-BRCA release",
     "description": ("What the frozen release holds (sample types, data layers, slide kinds, clinical and survival "
                     "fields with their exact values and counts) and the JSON step format of a cohort definition. "
                     "Give table and field for one field's full value list."),
     "inputSchema": {"type": "object", "properties": {
         "table": {"type": "string", "enum": ["clinical", "survival"], "description": "With field: the field's table."},
         "field": {"type": "string", "description": "A field name, e.g. er_status, pam50, stage, age, histology, os."}}},
     "annotations": _READ_ONLY},
    {"name": "draft_cohort", "title": "Draft a cohort definition",
     "description": ("Turn a plain-English request into an editable cohort definition (JSON steps, plus each step in "
                     "words, notes and words that weren't used) without running it. Run it, edited or not, with "
                     "build_cohort(definition=...)."),
     "inputSchema": {"type": "object", "properties": {"request": _REQUEST}, "required": ["request"]},
     "annotations": {**_READ_ONLY, "idempotentHint": False}},
    {"name": "build_cohort", "title": "Build a cohort",
     "description": ("Build a cohort from a plain-English request or a definition: run every step, report rows, "
                     "samples and patients per step, fix any join that counts a sample more than once, and return "
                     "the verdict with its checks, the sample IDs and the final definition (to rerun or export "
                     "exactly). Give request or definition."),
     "inputSchema": {"type": "object", "properties": {
         "request": _REQUEST, "definition": _DEFINITION,
         "include_sample_ids": {"type": "boolean", "default": True,
                                "description": "Return the sample IDs (default true)."}}},
     "annotations": {**_READ_ONLY, "idempotentHint": False}},
    {"name": "get_cohort_file", "title": "Export a cohort file",
     "description": ("One export for a definition (as returned by build_cohort): cohort_samples.csv (one row per "
                     "sample, sample_id column), reproduce_cohort.py (rebuilds the cohort exactly from the frozen "
                     "files), cohort_spec.json, ledger.csv, exclusions.csv, sample_selection.csv, coverage.csv, "
                     "verdict.json or findings.json."),
     "inputSchema": {"type": "object", "properties": {"definition": _DEFINITION,
                                                      "file": {"type": "string", "enum": list(FILES)}},
                     "required": ["definition", "file"]},
     "annotations": _READ_ONLY},
]


class ToolError(Exception):
    """A tool couldn't do what was asked; the message goes back to the agent (isError), not as a protocol error."""


class InvalidParams(Exception):
    pass


def _jsonable(obj):
    return json.loads(json.dumps(obj, default=lambda o: o.item() if hasattr(o, "item") else str(o)))


def step_format():
    from .assistant.prompt import RULES
    start, end = RULES.find("STEPS (all combined with AND)"), RULES.find("OUTPUT:")
    return RULES[start:end].strip() if 0 <= start < end else ""


class CohortTools:
    def __init__(self, store, drafter=None, catalog=None):
        self.store, self._catalog = store, catalog
        self.drafter = drafter or (lambda request: draft_spec(request, store, log=lambda m: print(m, file=sys.stderr)))

    @property
    def catalog(self):
        if self._catalog is None:
            from .assistant import Catalog
            self._catalog = Catalog.for_store(self.store)
        return self._catalog

    def call(self, name, args):
        """Returns (structured result, text for the agent)."""
        handler = {"describe_release": self.describe_release, "draft_cohort": self.draft_cohort,
                   "build_cohort": self.build_cohort, "get_cohort_file": self.get_cohort_file}.get(name)
        if handler is None:
            raise InvalidParams(f"Unknown tool: {name}")
        if not isinstance(args, dict):
            raise InvalidParams("Tool arguments must be an object.")
        missing = [k for k in TOOL_REQUIRED[name] if args.get(k) in (None, "")]
        if missing:
            raise ToolError(f"{name} needs {', '.join(missing)}.")
        return handler(**{k: v for k, v in args.items() if k in TOOL_ARGS[name]})

    # ---------- tools ----------
    def describe_release(self, table=None, field=None):
        if field:
            table = table or "clinical"
            prof = self.catalog.profile(table, str(field))
            if prof is None:
                close = [f for score, f in self.catalog.best_fields(table, str(field)) if score >= 0.5]
                raise ToolError(f"No field '{field}' in {table}." + (f" Closest: {', '.join(close)}." if close else ""))
            out = {"table": table, "field": self.catalog.friendly(table, str(field)), "profile": _jsonable(prof)}
            return out, json.dumps(out, indent=1)
        out = {"release": self.catalog.static_context(), "step_format": step_format(),
               "files_present": {n: bool(s["present"]) for n, s in self.store.file_status.items()}}
        return out, out["release"] + "\n\n" + out["step_format"]

    def draft_cohort(self, request):
        request = self._request(request)
        spec, route = self.drafter(request)
        out = {"drafted_by": route["label"], "definition": _jsonable(spec),
               "steps_in_words": [describe_step(s) for s in spec["steps"]], "notes": spec.get("notes", []),
               "unused_words": spec.get("unparsed", []), "unconfirmed_readings": spec.get("unconfirmed", []),
               "problems": [m for _, m in validate(spec, self.store)]}
        return out, json.dumps(out, indent=1)

    def build_cohort(self, request=None, definition=None, include_sample_ids=True):
        if definition is not None:
            if not isinstance(definition, dict) or not isinstance(definition.get("steps"), list):
                raise ToolError("definition must be an object with a 'steps' list.")
            query = definition
        else:
            query = self._request(request)
        try:
            run = build_cohort(query, store=self.store, write=False, drafter=self.drafter)
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        r, v = run.result, run.verdict
        out = {"request": run.request, "drafted_by": run.route["label"], "ok": run.ok,
               "n_samples": run.n_samples, "n_patients": int(r.final_patients) if run.ok else 0,
               "verdict": v.verdict, "verdict_reason": v.reason,
               "steps": [{"step": s.index + 1, "what": s.label, "rows": s.rows_out, "samples": s.samples_out,
                          "patients": s.patients_out, **({"error": s.error} if s.error else {})} for s in r.steps],
               "inflation_fixed": run.fixes,
               "inflated_before_fix": [{"step": s.index + 1, "note": s.inflation_note}
                                       for s in run.first_result.steps if s.inflated],
               "checks": [{"name": c.name, "status": c.status, "detail": c.detail} for c in v.checks],
               "human_review": v.human_review, "notes": run.spec.get("notes", []),
               "unused_words": run.spec.get("unparsed", []),
               "unconfirmed_readings": run.spec.get("unconfirmed", []), "definition": _jsonable(run.spec)}
        if include_sample_ids:
            out["sample_ids"] = run.sample_ids
        text = {k: v for k, v in out.items() if k not in ("sample_ids", "definition")}
        if include_sample_ids:
            text["sample_ids"] = f"{run.n_samples} IDs in structuredContent.sample_ids" if run.n_samples > 60 \
                else run.sample_ids
        return out, json.dumps(text, indent=1)

    def get_cohort_file(self, definition, file):
        if file not in FILES:
            raise ToolError(f"file must be one of {', '.join(FILES)}.")
        if not isinstance(definition, dict) or not isinstance(definition.get("steps"), list):
            raise ToolError("definition must be an object with a 'steps' list (the one build_cohort returned).")
        run = build_cohort(definition, store=self.store, write=False)
        r, v = run.result, run.verdict
        content = {
            "cohort_samples.csv": lambda: exports.cohort_frame(r).to_csv(index=False),
            "reproduce_cohort.py": lambda: exports.reproduce_script(r, self.store),
            "cohort_spec.json": lambda: json.dumps(r.spec, indent=2),
            "ledger.csv": lambda: exports.ledger_frame(r).to_csv(index=False),
            "exclusions.csv": lambda: exports.exclusions_frame(r).to_csv(index=False),
            "sample_selection.csv": lambda: exports.selection_frame(r).to_csv(index=False),
            "coverage.csv": lambda: exports.coverage_frame(r, self.store).to_csv(index=False),
            "verdict.json": lambda: json.dumps(v.to_dict(), indent=2),
            "findings.json": lambda: json.dumps(exports.findings_document(r, v, self.store, "Cohort Studio team"),
                                                indent=2),
        }[file]()
        return {"file": file, "n_samples": run.n_samples, "content": content}, content

    @staticmethod
    def _request(request):
        request = str(request or "").strip()
        if not request:
            raise ToolError("Give request (plain English) or definition.")
        if len(request) > MAX_REQUEST_CHARS:
            raise ToolError(f"The request is longer than {MAX_REQUEST_CHARS} characters.")
        return request


TOOL_ARGS = {t["name"]: set(t["inputSchema"]["properties"]) for t in TOOLS}
TOOL_REQUIRED = {t["name"]: t["inputSchema"].get("required", []) for t in TOOLS}


class MCPServer:
    """JSON-RPC 2.0 for MCP: one message (or a batch) in, the reply (or None for notifications) out."""

    def __init__(self, tools):
        self.tools = tools

    @staticmethod
    def error(msg_id, code, message):
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    def handle_payload(self, payload):
        if isinstance(payload, list):
            replies = [r for r in (self.handle(m) for m in payload) if r is not None]
            return replies or None
        return self.handle(payload)

    def handle(self, msg):
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return self.error(None, -32600, "Invalid Request")
        if "method" not in msg or "id" not in msg:
            return None                                    # a notification, or a response to us: nothing to send
        msg_id, method, params = msg["id"], msg["method"], msg.get("params") or {}
        try:
            if method == "initialize":
                asked = params.get("protocolVersion")
                result = {"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                          "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER_INFO,
                          "instructions": INSTRUCTIONS}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                result = self._call(params)
            else:
                return self.error(msg_id, -32601, f"Method not found: {method}")
        except InvalidParams as exc:
            return self.error(msg_id, -32602, str(exc))
        except Exception as exc:  # never leak a traceback; tool problems are reported inside _call
            print(f"cohort-studio MCP: {type(exc).__name__}: {exc}", file=sys.stderr)
            return self.error(msg_id, -32603, "Internal error")
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _call(self, params):
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            raise InvalidParams("tools/call needs a tool name.")
        try:
            structured, text = self.tools.call(params["name"], params.get("arguments") or {})
        except ToolError as exc:
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        return {"content": [{"type": "text", "text": text}], "structuredContent": structured, "isError": False}


def serve_stdio(server, stdin=None, stdout=None):
    """Newline-delimited JSON-RPC on stdin/stdout; anything else (progress, errors) goes to stderr. The real streams
    are read and written as UTF-8 bytes, so a Windows console encoding, a byte-order mark or \\r\\n never get in."""
    if stdin is None:
        stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8-sig")
    if stdout is None:
        stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="\n", write_through=True)
    for line in stdin:
        line = line.strip().lstrip("﻿")
        if not line:
            continue
        try:
            reply = server.handle_payload(json.loads(line))
        except json.JSONDecodeError:
            reply = MCPServer.error(None, -32700, "Parse error")
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m cohort_studio.mcp_server", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", help="folder with sample_manifest.csv (default: data/tcga-brca)")
    ap.add_argument("--rules", action="store_true", help="built-in parser only: no AI, nothing leaves this computer")
    ap.add_argument("--no-hosted", action="store_true", help="your own key or local model only, never the hosted service")
    args = ap.parse_args(argv)
    store = DataStore(args.data_dir)
    ai = "off" if args.rules else "own" if args.no_hosted else "auto"
    tools = CohortTools(store, drafter=lambda request: draft_spec(request, store, ai=ai,
                                                                  log=lambda m: print(m, file=sys.stderr)))
    print("Cohort Studio MCP server ready (stdio).", file=sys.stderr)
    serve_stdio(MCPServer(tools))
    return 0


if __name__ == "__main__":
    sys.exit(main())
