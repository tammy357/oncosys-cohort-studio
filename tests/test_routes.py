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


PR_POS = {"op": "filter", "table": "clinical", "field": "pr_status", "cmp": "in", "value": ["Positive"]}
TWO_ARMS = "ER-positive versus ER-negative primary tumours"
SPLIT = "ER-positive primary tumours split into training and test sets"


def _checks(spec, store):
    from cohort_studio import engine, verdict
    return {c.name: c for c in verdict.evaluate(engine.run(spec, store), store).checks}


def test_review_never_swaps_in_a_cohort_with_no_samples(store):
    # the parser ANDs 'ER-positive' with 'ER-negative' (0 samples); a review that prefers it is overruled
    client = ScriptedClient({TWO_ARMS: ai([PT, JC, ER_POS]), f"REVIEW: {TWO_ARMS}": {"choice": "rules", "reason": "B."}})
    d = make_draft(TWO_ARMS, store, client, compare_with_rules=True)
    assert d["reconcile"]["decision"] == "ai" and d["reconcile"]["rules_samples"] == 0 and ER_POS in d["steps"]
    assert "select no samples" in d["reconcile"]["note"]
    merge = ScriptedClient({TWO_ARMS: ai([PT, JC, ER_POS]), f"REVIEW: {TWO_ARMS}": {
        "choice": "merge", "reason": "x", "steps": parse_request(TWO_ARMS, store)["steps"]}})
    assert make_draft(TWO_ARMS, store, merge, compare_with_rules=True)["reconcile"]["decision"] == "ai"


def test_an_ai_draft_with_no_samples_never_replaces_a_parser_draft_with_some(store):
    empty = [PT, JC, ER_POS, {**ER_POS, "value": ["Negative"]}]            # ER-positive AND ER-negative: nobody
    client = ScriptedClient({REQUEST: ai(empty), f"REVIEW: {REQUEST}": {"choice": "ai", "reason": "A."}})
    d = make_draft(REQUEST, store, client, compare_with_rules=True)
    assert client.calls == 1 and d["reconcile"]["decision"] == "rules" and d["reconcile"]["ai_samples"] == 0
    assert "selects no samples" in d["reconcile"]["note"]
    assert llm.spec_from_draft(d)["steps"] == parse_request(REQUEST, store)["steps"]


def test_review_audit_flags_parts_no_step_carries_out(store):
    """A draft that keeps one arm of a comparison: the review names the dropped part, the verdict can't say Ready."""
    review = {"choice": "ai", "reason": "B selects nothing.", "not_done": ["versus ER-negative", "made-up words"],
              "unsettled": [{"phrase": "ER-positive", "read_as": "recorded ER status Positive"}]}
    client = ScriptedClient({TWO_ARMS: ai([PT, JC, ER_POS]), f"REVIEW: {TWO_ARMS}": review})
    spec = llm.spec_from_draft(make_draft(TWO_ARMS, store, client, compare_with_rules=True))
    assert spec["unparsed"] == ["versus er-negative"]                 # quoted from the request; invented words dropped
    assert spec["unconfirmed"] == []                                  # the parser reads 'ER-positive' literally
    assert _checks(spec, store)["The whole request was turned into steps"].status == "unknown"


def test_review_audit_cant_contradict_what_the_ai_draft_maps(store):
    """The review is a second opinion: a part the AI draft maps to a step is never reported as missing."""
    review = {"choice": "ai", "reason": "A keeps ER+.", "not_done": ["versus ER-negative"]}
    mapped = ai([PT, JC, ER_POS], mappings=[{"phrase": "ER-positive versus ER-negative", "meaning": "ER+ arm"}])
    spec = llm.spec_from_draft(make_draft(TWO_ARMS, store, ScriptedClient({TWO_ARMS: mapped,
                                                                          f"REVIEW: {TWO_ARMS}": review}),
                                          compare_with_rules=True))
    assert spec["unparsed"] == []


def test_same_cohort_is_audited_only_when_words_went_unused_by_both(store):
    # same cohort, but 'split into training and test sets' is used by neither draft: one audit call
    audit = {"choice": "ai", "reason": "same", "not_done": ["split into training and test sets"]}
    client = ScriptedClient({SPLIT: ai([PT, JC, ER_POS], mappings=[{"phrase": "ER-positive", "meaning": "ER+"}]),
                             f"REVIEW: {SPLIT}": audit})
    spec = llm.spec_from_draft(make_draft(SPLIT, store, client, compare_with_rules=True))
    assert client.calls == 2 and spec["steps"][-1] == ER_POS and spec["unparsed"] == ["split into training and test sets"]
    # the AI already said the split isn't a step: nothing is left unaccounted for, so no extra call
    said = ScriptedClient({SPLIT: ai([PT, JC, ER_POS], unsupported=[
        {"phrase": "split into training and test sets", "reason": "splitting is not a step"}])})
    d = make_draft(SPLIT, store, said, compare_with_rules=True)
    assert said.calls == 1 and d["reconcile"]["decision"] == "same"


def test_parser_draft_cant_replace_one_that_carries_out_words_the_parser_skipped(store):
    # the parser skips 'PgR-negative'; the AI maps it to a step; a review preferring the parser is overruled
    req = "ER-positive primary tumours that are PgR-negative"
    pr_neg = {**PR_POS, "value": ["Negative"]}
    assert "PgR-negative" in parse_request(req, store)["unparsed"]
    client = ScriptedClient({req: ai([PT, JC, ER_POS, pr_neg], mappings=[{"phrase": "PgR-negative", "meaning": "PR-"}]),
                             f"REVIEW: {req}": {"choice": "rules", "reason": "B."}})
    d = make_draft(req, store, client, compare_with_rules=True)
    assert d["reconcile"]["decision"] == "ai" and pr_neg in d["steps"] and "'PgR-negative'" in d["reconcile"]["note"]


def test_a_number_the_parser_cant_place_is_an_unconfirmed_reading(store):
    # '50+' says nothing about what is 50: the parser leaves it, the AI reads it as an age, a person must confirm
    req = "ER-positive primary tumours for 50+"
    age = {"op": "filter", "table": "clinical", "field": "age", "cmp": "gte", "value": 50}
    assert "50+" in parse_request(req, store)["unparsed"]
    client = ScriptedClient({req: ai([PT, JC, ER_POS, age], mappings=[{"phrase": "50+", "meaning": "age 50 or older"}]),
                             f"REVIEW: {req}": {"choice": "ai", "reason": "B ignores 50+."}})
    spec = llm.spec_from_draft(make_draft(req, store, client, compare_with_rules=True))
    assert any(u.startswith("50+ (read as: ") for u in spec["unconfirmed"]) and not spec["unparsed"]
    assert _checks(spec, store)["Every open reading of the request was confirmed"].status == "unknown"
    # stated plainly, the parser places the number itself and nothing is left to confirm
    plain = "ER-positive primary tumours from patients aged 50 or older"
    assert not [w for w in parse_request(plain, store)["unparsed"] if w[0].isdigit()]


def test_an_answer_with_no_steps_is_a_real_answer(store):
    q = {"id": "q1", "phrase": "hormone", "question": "Use PR as a stand-in?", "options": [
        {"label": "PR-positive as a stand-in", "steps": [PR_POS]}, {"label": "Leave it out", "steps": []}]}
    d = make_draft("hormone treated primary tumours", store, ScriptedClient({"hormone treated primary tumours": ai([PT], clarifications=[q])}))
    assert [o["label"] for o in d["clarifications"][0]["options"]] == ["PR-positive as a stand-in", "Leave it out"]
    assert PR_POS not in d["steps"]                                # nothing is applied before someone answers


def test_the_only_answer_that_runs_is_used_but_left_unconfirmed(store):
    q = {"id": "q1", "phrase": "hormone", "question": "Which stand-in?", "options": [
        {"label": "PR-positive", "steps": [PR_POS]},
        {"label": "Smoking history", "steps": [{"op": "filter", "table": "clinical", "field": "smoking", "cmp": "in",
                                                "value": ["yes"]}]}]}
    req = "hormone treated primary tumours"
    spec = llm.spec_from_draft(make_draft(req, store, ScriptedClient({req: ai([PT], clarifications=[q])})))
    assert PR_POS in spec["steps"] and any("PR-positive" in u for u in spec["unconfirmed"])
    assert _checks(spec, store)["Every open reading of the request was confirmed"].status == "unknown"


def test_a_step_that_is_an_answer_is_not_also_applied_to_every_answer(store):
    # the model left ER+ in the base steps and also offered it as an answer: each answer is counted on its own
    q = {"id": "q1", "phrase": "receptor positive", "question": "Which receptor?", "options": [
        {"label": "ER-positive", "steps": [ER_POS]}, {"label": "PR-positive", "steps": [PR_POS]}]}
    req = "receptor positive primary tumours"
    d = make_draft(req, store, ScriptedClient({req: ai([PT, JC, ER_POS], clarifications=[q])}))
    assert ER_POS not in d["steps"] and [o["n_samples"] for o in d["clarifications"][0]["options"]] == [4, 6]


def test_a_value_listed_twice_is_listed_once():
    from cohort_studio.assistant.verify import canonicalize
    steps, _ = canonicalize([PT, JC, {**ER_POS, "value": ["Positive", "Negative", "Positive"]}])
    assert steps[-1]["value"] == ["Positive", "Negative"]


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
