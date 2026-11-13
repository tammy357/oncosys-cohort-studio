"""Which route drafts a request (own model -> hosted service -> built-in parser), the AI-vs-parser review, and building
cohorts from Python, files and the command line. No network and no real model: scripted stand-ins only."""
import csv
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cohort_studio import hosted, llm  # noqa: E402
from cohort_studio.__main__ import main  # noqa: E402
from cohort_studio.api import build_cohort, load_queries  # noqa: E402
from cohort_studio.assistant import Catalog, ScriptedClient, make_draft  # noqa: E402
from cohort_studio.data import DataStore  # noqa: E402
from cohort_studio.parse import parse_request  # noqa: E402
from tests.make_fixture import build  # noqa: E402

PT = {"op": "sample_type", "values": ["Primary Tumor"]}
JC = {"op": "join", "table": "clinical", "key": "sample_id", "mode": "rows"}
ER_POS = {"op": "filter", "table": "clinical", "field": "er_status", "cmp": "in", "value": ["Positive"]}
REQUEST = "ER-positive primary tumours"
KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_BASE_URL", "COHORT_STUDIO_PROVIDER", "COHORT_STUDIO_API_TOKEN")


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    return build(tmp_path_factory.mktemp("release"))


@pytest.fixture(scope="module")
def store(data_dir):
    return DataStore(data_dir)


@pytest.fixture()
def no_keys(monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)


def ai(steps, **extra):
    return {"interpretation": "scripted", "steps": steps, **extra}


# ---------- the AI draft is checked against the built-in parser's ----------
def test_same_cohort_needs_no_review_call(store):
    client = ScriptedClient({REQUEST: ai([PT, JC, ER_POS])})
    d = make_draft(REQUEST, store, client, compare_with_rules=True)
    assert client.calls == 1 and d["reconcile"]["decision"] == "same"


def test_review_can_keep_the_parser_draft(store):
    rules = parse_request(REQUEST, store)["steps"]
    added = [PT, JC, ER_POS, {"op": "layer", "layer": "protein"}]          # the AI added a constraint nobody asked for
    client = ScriptedClient({REQUEST: ai(added), f"REVIEW: {REQUEST}": {"choice": "rules", "reason": "A added protein."}})
    d = make_draft(REQUEST, store, client, compare_with_rules=True)
    assert client.calls == 2 and d["reconcile"]["decision"] == "rules"
    spec = llm.spec_from_draft(d)
    assert spec["steps"] == rules and any("kept the parser's steps" in n for n in spec["notes"])


def test_review_mix_may_only_reuse_steps_from_the_two_drafts(store):
    rules = parse_request(REQUEST, store)["steps"]
    added = [PT, JC, ER_POS, {"op": "layer", "layer": "protein"}]
    mix = ScriptedClient({REQUEST: ai(added), f"REVIEW: {REQUEST}": {"choice": "merge", "steps": rules, "reason": "B."}})
    assert make_draft(REQUEST, store, mix, compare_with_rules=True)["reconcile"]["decision"] == "merge"
    invented = ScriptedClient({REQUEST: ai(added), f"REVIEW: {REQUEST}": {"choice": "merge", "reason": "x",
                                                                          "steps": [PT, {"op": "layer", "layer": "rna"}]}})
    d = make_draft(REQUEST, store, invented, compare_with_rules=True)
    assert d["reconcile"]["decision"] == "ai" and {"op": "layer", "layer": "protein"} in d["steps"]


def test_a_failed_or_unclear_review_keeps_the_ai_draft(store):
    added = [PT, JC, ER_POS, {"op": "layer", "layer": "protein"}]
    for review in ({f"REVIEW: {REQUEST}": {"nonsense": True}}, {}):          # unclear reply; no reply (AIError)
        d = make_draft(REQUEST, store, ScriptedClient({REQUEST: ai(added), **review}), compare_with_rules=True)
        assert d["reconcile"]["decision"] == "ai" and {"op": "layer", "layer": "protein"} in d["steps"]


def test_open_questions_in_the_web_app_are_left_to_the_user(store):
    q = {"id": "q1", "phrase": "ER", "question": "Which?", "options": [
        {"label": "ER+", "steps": [ER_POS]}, {"label": "ER-", "steps": [{**ER_POS, "value": ["Negative"]}]}]}
    client = ScriptedClient({REQUEST: ai([PT], clarifications=[q])})
    d = make_draft(REQUEST, store, client, compare_with_rules=True, interactive=True)
    assert client.calls == 1 and "reconcile" not in d and d["clarifications"]


# ---------- the route: own model -> hosted service -> built-in parser ----------
def test_own_model_comes_first(store, monkeypatch):
    monkeypatch.setattr(hosted, "draft", lambda r: pytest.fail("the hosted service must not be called"))
    spec, route = llm.draft_spec(REQUEST, store, client=ScriptedClient({REQUEST: ai([PT, JC, ER_POS])}))
    assert route["used"] == "own" and spec["parser"].startswith("ai:")


def test_hosted_service_when_there_is_no_key(store, monkeypatch, no_keys):
    monkeypatch.setenv("COHORT_STUDIO_HOSTED", "on")
    served = {"spec": {"request": REQUEST, "steps": [PT, JC, ER_POS], "notes": [], "unparsed": [], "parser": "ai:x"},
              "problems": [], "model": "openai:gpt-4.1-mini", "limits": {"requests_left_today": 9}}
    monkeypatch.setattr(hosted, "_call", lambda method, path, payload=None, timeout=None: json.loads(json.dumps(served)))
    spec, route = llm.draft_spec(REQUEST, store)
    assert route["used"] == "hosted" and route["limits"]["requests_left_today"] == 9
    assert spec["steps"] == [PT, JC, ER_POS] and "hosted" in spec["notes"][0]


def test_parser_when_the_service_is_unreachable_or_its_draft_cant_run(store, monkeypatch, no_keys):
    monkeypatch.setenv("COHORT_STUDIO_HOSTED", "on")

    def offline(*a, **k):
        raise hosted.HostedUnavailable("the hosted AI service can't be reached (no network)")
    monkeypatch.setattr(hosted, "_call", offline)
    spec, route = llm.draft_spec(REQUEST, store)
    assert route["used"] == "rules" and spec["parser"] == "rules"
    assert any("can't be reached" in n and "built-in parser" in n for n in spec["notes"])
    bad = {"spec": {"request": REQUEST, "steps": [PT, {"op": "filter", "table": "clinical", "field": "smoking",
                                                       "cmp": "in", "value": ["yes"]}]}}
    monkeypatch.setattr(hosted, "_call", lambda *a, **k: bad)
    assert llm.draft_spec(REQUEST, store)[1]["used"] == "rules"


def test_rules_only_sends_nothing(store, monkeypatch):
    monkeypatch.setattr(hosted, "_call", lambda *a, **k: pytest.fail("nothing may be sent with ai='off'"))
    monkeypatch.setattr(llm, "own_model", lambda *a, **k: pytest.fail("no model with ai='off'"))
    spec, route = llm.draft_spec(REQUEST, store, ai="off")
    assert route["used"] == "rules" and not any("AI" in n for n in spec["notes"])


# ---------- building cohorts: Python, files and the command line ----------
def _sample_ids(path):
    rows = list(csv.reader(open(path)))
    return rows[0], [r[0] for r in rows[1:]]


def test_a_definition_runs_exactly_and_exports_one_row_per_sample(store, tmp_path):
    slides = {"op": "join", "table": "slides", "key": "sample_id", "mode": "rows", "slide_kinds": ["diagnostic (FFPE)"]}
    run = build_cohort({"request": "pt with a dx slide", "steps": [PT, slides]}, out_dir=tmp_path / "out", store=store)
    assert run.route["used"] == "definition" and run.fixes                # the slide join inflated and was fixed
    header, ids = _sample_ids(tmp_path / "out" / "cohort_samples.csv")
    assert header == ["sample_id"] and len(ids) == len(set(ids)) == run.n_samples > 0
    assert all(len(i) == 15 for i in ids) and (tmp_path / "out" / "reproduce_cohort.py").exists()


def test_cli_runs_a_file_of_requests_and_definitions(data_dir, tmp_path):
    queries = tmp_path / "cohorts.json"
    queries.write_text(json.dumps({"cohorts": [REQUEST, {"name": "dx slides", "steps": [
        PT, {"op": "join", "table": "slides", "key": "sample_id", "mode": "rows", "slide_kinds": []}]}]}))
    assert len(load_queries(queries)) == 2
    assert main(["--file", str(queries), "--out", str(tmp_path / "out"), "--rules", "--data-dir", str(data_dir)]) == 0
    summary = list(csv.DictReader(open(tmp_path / "out" / "summary.csv")))
    assert [r["drafted by"] for r in summary] == ["built-in parser (AI switched off)", "cohort definition, run as written"]
    for r in summary:
        header, ids = _sample_ids(pathlib.Path(r["folder"]) / "cohort_samples.csv")
        assert len(ids) == len(set(ids)) == int(r["samples"]) > 0


def test_an_ai_draft_that_fails_here_is_redrafted_by_the_parser(store, tmp_path, monkeypatch):
    from cohort_studio import api
    broken = {"request": REQUEST, "steps": [PT, JC, {**ER_POS, "cmp": "gte", "value": "not a number"}], "notes": [],
              "unparsed": [], "parser": "ai:x"}
    monkeypatch.setattr(api, "draft_spec", lambda *a, **k: (broken, {"used": "hosted", "label": "hosted", "tried": []}))
    run = build_cohort(REQUEST, out_dir=tmp_path / "out", store=store)
    assert run.ok and run.route["used"] == "rules" and any("didn't run here" in n for n in run.spec["notes"])
