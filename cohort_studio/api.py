"""Build a cohort from Python or a file, exactly as the command line does.

    from cohort_studio.api import build_cohort
    run = build_cohort("primary tumors with both RNA and protein data and a diagnostic slide", out_dir="output")
    print(run.n_samples, run.route["label"], run.out_dir / "cohort_samples.csv")

A query is plain English (drafted by the AI route in cohort_studio/llm.py: your own key, else the hosted service,
else the built-in parser) or a cohort definition: a dict or JSON file with "steps" (for example a cohort_spec.json
exported earlier), which runs exactly as written. Every run applies the one-click inflation fix, evaluates the
verdict and writes cohort_samples.csv (one row per sample, a sample_id column with the 15-character TCGA barcode),
reproduce_cohort.py and the other exports to out_dir.
"""
import copy
import dataclasses
import json
import pathlib
import re

from . import engine, exports, verdict
from .data import DataStore
from .llm import draft_spec
from .parse import parse_request
from .spec import new_spec


@dataclasses.dataclass
class CohortRun:
    request: str
    spec: dict
    result: object                  # engine.RunResult after the inflation fix
    first_result: object            # before the fix (the same object when nothing inflated)
    fixes: list
    verdict: object
    route: dict
    out_dir: pathlib.Path = None

    @property
    def sample_ids(self):
        return list(self.result.cohort_ids)

    @property
    def n_samples(self):
        return len(self.result.cohort_ids)

    @property
    def ok(self):
        return self.result.ok


def as_query(item):
    """("text", request) or ("spec", spec) for a string, {"request": ...}, or a definition with "steps"."""
    if isinstance(item, str):
        return "text", item.strip()
    if isinstance(item, dict) and isinstance(item.get("steps"), list):
        spec = {**new_spec(item.get("request", ""), parser=item.get("parser", "definition")), **copy.deepcopy(item)}
        return "spec", spec
    if isinstance(item, dict) and str(item.get("request", "")).strip():
        return "text", str(item["request"]).strip()
    raise ValueError(f"Not a cohort request or definition: {json.dumps(item)[:120]}")


def load_queries(path):
    """The queries in a file: .txt = one plain-English request per line ('#' lines are skipped); .json = one query,
    a list of them, or {"cohorts": [...]}; each query is a request string, {"request": ...} or a definition with
    "steps" (an optional "name" names its output folder)."""
    path = pathlib.Path(path)
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() != ".json":
        return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    data = json.loads(text)
    if isinstance(data, dict) and isinstance(data.get("cohorts"), list):
        data = data["cohorts"]
    return data if isinstance(data, list) else [data]


def folder_name(i, item):
    name = item.get("name") if isinstance(item, dict) else None
    words = name or (item if isinstance(item, str) else item.get("request", "")) or "cohort"
    return f"{i:02d}-" + (re.sub(r"[^a-z0-9]+", "-", str(words).lower()).strip("-")[:48] or "cohort")


def _run(spec, store, fix):
    first = engine.run(spec, store)
    if fix and any(s.inflated for s in first.steps):
        spec, changes = engine.apply_fixes(spec, first)
        return spec, engine.run(spec, store), first, changes
    return spec, first, first, []


def build_cohort(query, out_dir="output", store=None, data_dir=None, ai="auto", fix=True, accept_unparsed=False,
                 team="Cohort Studio team", write=True, log=None, drafter=None, **ai_options):
    """Draft (if needed), run, fix inflation, evaluate and export one cohort. ai: "auto", "own" or "off" (see
    llm.draft_spec); ai_options: provider, api_key, model, base_url or client for your own model. drafter: a
    function request -> (spec, route) used instead of llm.draft_spec (the hosted MCP server drafts on the server)."""
    store = store or DataStore(data_dir)
    kind, q = as_query(query)
    if kind == "spec":
        spec, route = q, {"used": "definition", "label": "cohort definition, run as written", "tried": []}
    elif drafter is not None:
        spec, route = drafter(q)
    else:
        spec, route = draft_spec(q, store, ai=ai, log=log, **ai_options)
    if accept_unparsed:
        spec["unparsed_confirmed"] = True
    spec, result, first, fixes = _run(spec, store, fix)
    if not result.ok and kind == "text" and route["used"] in ("own", "hosted"):
        # The AI draft passed the checks but a step failed on this computer's data: draft it with the parser instead.
        error = next((s.error for s in result.steps if s.error), "a step failed")
        rules = parse_request(q, store)
        rules["notes"].append(f"The AI draft didn't run here ({error}), so the built-in parser drafted the steps.")
        rules["unparsed_confirmed"] = bool(accept_unparsed)
        r_spec, r_result, r_first, r_fixes = _run(rules, store, fix)
        if r_result.ok:
            spec, result, first, fixes = r_spec, r_result, r_first, r_fixes
            route = {"used": "rules", "label": "built-in parser (the AI draft didn't run here)",
                     "tried": route["tried"] + [f"the AI draft didn't run: {error}"]}
    v = verdict.evaluate(result, store)
    out = exports.write_all(result, v, store, out_dir, team=team) if write else None
    return CohortRun(spec.get("request", q if kind == "text" else ""), spec, result, first, fixes, v, route, out)
