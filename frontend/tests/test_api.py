"""Web API tests: run with  python -m pytest frontend/tests -q  (from the repository root).

They use the synthetic fixture release and a scripted stand-in model, so they need no download, key or network.
"""
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
JC = {"op": "join", "table": "clinical", "key": "sample_id", "mode": "rows"}


def F(field, cmp, value):
    return {"op": "filter", "table": "clinical", "field": field, "cmp": cmp, "value": value}


REPLIES = {
    "hr positive tumours": {
        "interpretation": "Hormone-receptor positive primary tumours", "steps": [PT],
        "mappings": [{"phrase": "tumours", "meaning": "primary tumours"}],
        "clarifications": [{"id": "q1", "phrase": "hr positive", "question": "How should HR-positive be defined?",
                            "why": "The definitions select different tumours.", "options": [
                                {"label": "ER+ or PR+", "steps": [{"op": "any_of", "table": "clinical", "conditions": [
                                    {"field": "er_status", "cmp": "in", "value": ["Positive"]},
                                    {"field": "pr_status", "cmp": "in", "value": ["Positive"]}]}]},
                                {"label": "ER+ only", "steps": [F("er_status", "in", ["Positive"])]}]}]},
    "tumours with a diagnostic slide": {"steps": [PT, {"op": "join", "table": "slides", "key": "sample_id", "mode": "rows",
                                                       "slide_kinds": ["diagnostic"]}],
                                        "mappings": [{"phrase": "diagnostic slide", "meaning": "diagnostic (FFPE) slides"}]},
    "smokers": {"steps": [PT], "unsupported": [{"phrase": "smokers", "reason": "No smoking field in this release."}]},
    # editing in words: the model returns the complete updated steps
    "only the basal ones": {"interpretation": "Kept ER-positive and added Basal-like only.",
                            "steps": [PT, JC, F("er_status", "in", ["Positive"]), F("pam50", "in", ["Basal"])]},
}


@pytest.fixture()
def client(tmp_path):
    data = build(tmp_path / "release")
    settings = Settings(data_dir=str(data), database_url=f"sqlite:///{(tmp_path / 'app.db').as_posix()}")
    return TestClient(create_app(settings, client=ScriptedClient(REPLIES)))


def test_page_and_meta(client):
    assert client.get("/").status_code == 200
    meta = client.get("/api/meta").json()
    assert meta["ai"]["available"] and len(meta["presets"]) >= 5
    assert {o["value"] for o in meta["advanced"]["sample_types"]} == {"Primary Tumor", "Solid Tissue Normal", "Metastatic"}


def test_question_answer_run_and_history(client):
    d = client.post("/api/draft", json={"query": "hr positive tumours"}).json()
    assert d["status"] == "needs_answers"
    assert [o["n_samples"] for o in d["clarifications"][0]["options"]] == [6, 4]
    assert client.post(f"/api/drafts/{d['draft_id']}/resolve", json={"answers": {}}).status_code == 400
    r = client.post(f"/api/drafts/{d['draft_id']}/resolve", json={"answers": {"q1": 1}}).json()
    res = client.post("/api/run", json={"spec": r["spec"], "query": "hr positive tumours", "draft_id": d["draft_id"]}).json()
    assert res["final"]["samples"] == 4 and res["search_id"]
    hist = client.get("/api/searches").json()["searches"]
    assert hist[0]["query"] == "hr positive tumours" and hist[0]["n_samples"] == 4


def test_inflation_blocks_export_until_fixed(client):
    d = client.post("/api/draft", json={"query": "tumours with a diagnostic slide"}).json()
    res = client.post("/api/run", json={"spec": d["spec"], "query": "slides"}).json()
    assert not res["exportable"] and any(s["inflated"] for s in res["steps"])
    sid = res["search_id"]
    assert client.get(f"/api/searches/{sid}/files/cohort_samples.csv").status_code == 409
    fixed = client.post(f"/api/searches/{sid}/fix", json={}).json()
    assert fixed["exportable"] and fixed["final"]["rows"] == fixed["final"]["samples"]
    csv = client.get(f"/api/searches/{sid}/files/cohort_samples.csv").text.strip().splitlines()
    assert csv[0] == "sample_id" and len(csv) - 1 == len(set(csv[1:])) == fixed["final"]["samples"]


def test_cannot_answer_and_rules_fallback(client):
    assert client.post("/api/draft", json={"query": "smokers"}).json()["status"] == "cannot_answer"
    d = client.post("/api/draft", json={"query": "ER-positive primary tumours", "use_ai": False}).json()
    assert d["mode"] == "rules" and d["spec"]["parser"] == "rules"


def test_edited_steps_are_rechecked(client):
    spec = {"request": "edited", "parser": "ai:scripted", "steps": [PT, F("er_status", "in", ["positive"])]}
    res = client.post("/api/run", json={"spec": spec, "edited": True}).json()
    assert res["final"]["samples"] == 4 and res["rechecked"]
    bad = {"request": "edited", "parser": "ai:scripted", "steps": [PT, F("smoking", "in", ["yes"])]}
    assert client.post("/api/run", json={"spec": bad, "edited": True}).status_code == 400


def test_advanced_search(client):
    d = client.post("/api/advanced", json={"form": {"hr_positive": True, "exclude_layers": ["protein"]}}).json()
    assert d["mode"] == "advanced" and d["preview"]["n_samples"] == 3   # patients 6, 7, 8


def test_favourites_rerun_without_the_model(client):
    d = client.post("/api/draft", json={"query": "tumours with a diagnostic slide"}).json()
    res = client.post("/api/run", json={"spec": d["spec"], "query": "slides"}).json()
    fav = client.post("/api/favorites", json={"search_id": res["search_id"]}).json()
    favs = client.get("/api/favorites").json()["favorites"]
    assert favs[0]["has_spec"]
    out = client.post(f"/api/favorites/{fav['id']}/run").json()
    assert out["kind"] == "result"
    assert client.delete(f"/api/favorites/{fav['id']}").status_code == 200


def test_suggestions_include_terms_counts_and_recent_searches(client):
    s = client.get("/api/suggest", params={"q": "tumours that are basa"}).json()["suggestions"]
    assert s[0]["text"] == "Basal-like" and s[0]["count"] == 2
    client.post("/api/draft", json={"query": "smokers"})
    d = client.post("/api/draft", json={"query": "tumours with a diagnostic slide"}).json()
    client.post("/api/run", json={"spec": d["spec"], "query": "tumours with a diagnostic slide"})
    recent = client.get("/api/suggest", params={"q": "diagnos"}).json()["suggestions"]
    assert recent[0]["category"] == "recent search"


def _run(client, steps, query):
    return client.post("/api/run", json={"spec": {"request": query, "steps": steps}, "query": query, "mode": "advanced"}).json()["search_id"]


def test_compare_two_cohorts(client):
    a = _run(client, [PT, JC, F("er_status", "in", ["Positive"])], "ER-positive")
    b = _run(client, [PT, JC, F("pam50", "in", ["LumA", "Basal"])], "LumA or Basal")
    d = client.get("/api/compare", params={"a": f"s:{a}", "b": f"s:{b}"}).json()
    assert (d["a"]["n_samples"], d["b"]["n_samples"]) == (4, 5)          # ER+ = 1,2,4,7 ; LumA/Basal = 1,3,4,5,7
    assert (d["overlap"]["shared"], d["overlap"]["only_a"], d["overlap"]["only_b"]) == (3, 1, 2)
    assert d["overlap"]["jaccard"] == 0.5
    assert {c["name"] for c in d["composition"]} >= {"PAM50 subtype", "ER status (IHC)"}
    assert d["protocol"]["shared"] == 2 and d["protocol"]["only_a"] == 1 and d["protocol"]["only_b"] == 1
    assert d["outcomes"]["os"]["a"]["n"] >= 1 and d["summary"]
    assert client.get("/api/compare", params={"a": f"s:{a}", "b": f"s:{a}"}).status_code == 400
    assert client.get("/api/compare", params={"a": f"s:{a}", "b": "s:9999"}).status_code == 404


def test_compare_insights_check_numbers(tmp_path):
    reply = {"headline": "A is all ER-positive; B mixes subtypes.",
             "differences": [{"title": "Size", "detail": "A has 4 samples and B has 5; 777 are in both."}],
             "similarities": [{"title": "Source", "detail": "Both are primary tumours."}],
             "applications": {"a": "ER-positive biology", "b": "Subtype contrasts"}, "cautions": ["Small cohorts."]}
    data = build(tmp_path / "release")
    settings = Settings(data_dir=str(data), database_url=f"sqlite:///{(tmp_path / 'app.db').as_posix()}")
    c = TestClient(create_app(settings, client=ScriptedClient({}, default=reply)))
    a = _run(c, [PT, JC, F("er_status", "in", ["Positive"])], "ER-positive")
    b = _run(c, [PT, JC, F("pam50", "in", ["LumA", "Basal"])], "LumA or Basal")
    out = c.post("/api/compare/insights", json={"a": f"s:{a}", "b": f"s:{b}"}).json()
    assert out["headline"] and out["applications"]["a"]
    assert out["unverified_numbers"] == ["777"]                          # 4 and 5 are real; 777 is invented


def test_advanced_live_preview(client):
    p = client.post("/api/advanced/preview", json={"form": {"er": "Positive"}}).json()
    assert p["ok"] and p["n_samples"] == 4
    bad = client.post("/api/advanced/preview", json={"form": {"extra_filters": [{"field": "smoking", "cmp": "in", "value": ["yes"]}]}}).json()
    assert not bad["ok"] and bad["error"]


def test_compare_page_is_served(client):
    assert client.get("/compare").status_code == 200


def test_users_see_only_their_own_history(client):
    d = client.post("/api/draft", json={"query": "tumours with a diagnostic slide"}, headers={"x-user": "alice"}).json()
    client.post("/api/run", json={"spec": d["spec"], "query": "alice's cohort"}, headers={"x-user": "alice"})
    assert client.get("/api/searches", headers={"x-user": "bob"}).json()["searches"] == []
    assert len(client.get("/api/searches", headers={"x-user": "alice"}).json()["searches"]) == 1


# ---------- editing a built cohort ----------
def _cid(step, cid, **extra):
    return {**step, "_cid": cid, **extra}


def test_preview_counts_impacts_and_changes(client):
    base = [PT, JC, F("er_status", "in", ["Positive"])]
    steps = [_cid(PT, 1), _cid(JC, 2), _cid(F("er_status", "in", ["positive"]), 3), _cid(F("pam50", "in", ["LumA", "Basal"]), 4)]
    p = client.post("/api/preview", json={"steps": steps, "base_steps": base}).json()
    assert p["ok"] and p["final"]["samples"] == 3                         # ER+ (1,2,4,7) and LumA/Basal (1,3,4,5,7)
    by = {s["cid"]: s for s in p["steps"]}
    assert by[4]["samples_in"] == 4 and by[4]["samples"] == 3 and by[4]["removed"] == 1
    assert p["impacts"] == {"3": 2, "4": 1}                               # without each filter the cohort grows by that much
    assert [c["kind"] for c in p["changes"]] == ["added"]
    assert p["fixes"] and p["fixes"][0]["to"] == "Positive"               # "positive" checked against the real values
    off = client.post("/api/preview", json={"steps": steps[:3] + [{**steps[3], "_off": True}], "base_steps": base}).json()
    assert off["final"]["samples"] == 4 and off["changes"] == []
    # a filter without its join still runs: the join is added
    nojoin = client.post("/api/preview", json={"steps": [_cid(PT, 1), _cid(F("er_status", "in", ["Positive"]), 2)]}).json()
    assert nojoin["final"]["samples"] == 4 and nojoin["impacts"] == {"2": 4}
    bad = client.post("/api/preview", json={"steps": [_cid(PT, 1), _cid(F("smoking", "in", ["yes"]), 2)]}).json()
    assert bad["problems"] and bad["problems"][0]["cid"] == 2


def test_versions_restore_compare_and_fork(client):
    sid = _run(client, [PT, JC, F("er_status", "in", ["Positive"])], "ER-positive")
    v = client.get(f"/api/searches/{sid}/versions").json()["versions"]
    assert [x["n"] for x in v] == [1] and v[0]["n_samples"] == 4
    new_steps = [PT, JC, F("er_status", "in", ["Positive"]), F("pam50", "in", ["LumA", "Basal"])]
    res = client.post(f"/api/searches/{sid}/versions", json={"steps": new_steps, "note": "Added PAM50"}).json()
    assert res["search_id"] == sid and res["final"]["samples"] == 3
    assert [(x["n"], x["n_samples"], x["note"]) for x in res["versions"]] == [(1, 4, "First run"), (2, 3, "Added PAM50")]
    same = client.post(f"/api/searches/{sid}/versions", json={"steps": new_steps}).json()
    assert len(same["versions"]) == 2                                     # nothing changed, so no new version
    v1 = client.get(f"/api/searches/{sid}/versions/1").json()
    assert len(v1["spec"]["steps"]) == 3 and v1["labels"]
    d = client.get("/api/compare", params={"a": f"v:{sid}:1", "b": f"v:{sid}:2"}).json()
    assert (d["a"]["n_samples"], d["b"]["n_samples"], d["overlap"]["shared"]) == (4, 3, 3)
    back = client.post(f"/api/searches/{sid}/versions", json={"steps": v1["spec"]["steps"], "source": "restore", "note": "Restored v1"}).json()
    assert back["final"]["samples"] == 4 and back["versions"][-1]["source"] == "restore"
    fork = client.post("/api/fork", json={"steps": new_steps, "query": "ER+ LumA/Basal", "base_search_id": sid}).json()
    assert fork["search_id"] != sid and fork["final"]["samples"] == 3
    assert client.get(f"/api/searches/{sid}").json()["final"]["samples"] == 4  # the original is untouched
    assert client.get("/api/searches/9999/versions").status_code == 404


def test_edit_in_words_returns_the_changes(client):
    current = [_cid(PT, 1), _cid(JC, 2), _cid(F("er_status", "in", ["Positive"]), 3), _cid(F("pam50", "in", ["LumA"]), 4, _off=True)]
    d = client.post("/api/draft", json={"query": "only the basal ones", "current_steps": current, "current_request": "ER-positive",
                                        "history": ["first change"]}).json()
    assert d["refines"] and d["spec"]
    assert [c["kind"] for c in d["diff"]] == ["added"] and "Basal" in d["diff"][0]["after"]
    p = client.post("/api/preview", json={"steps": d["spec"]["steps"]}).json()
    assert d["preview"]["n_samples"] == p["final"]["samples"]              # the reply's count is the real count
    # without the model, editing in words is refused (the steps editor still works)
    off = client.post("/api/draft", json={"query": "only the basal ones", "use_ai": False, "current_steps": current})
    assert off.status_code == 400


def test_result_carries_composition_for_the_funnel(client):
    sid = _run(client, [PT, JC, F("er_status", "in", ["Positive"])], "ER-positive")
    res = client.get(f"/api/searches/{sid}").json()
    comp = res["composition"]["ER status (IHC)"]
    assert len(comp["steps"]) == len(res["steps"]) and comp["final"] == {"Positive": 4}
    assert sum(comp["start"].values()) == res["start"]["samples"]
    removed = res["steps"][-1]
    assert removed["removed"] and sum(removed["removed_by"]["ER status (IHC)"].values()) == removed["removed"]
    assert res["versions"][0]["n"] == 1


def test_one_sample_per_patient_choices_and_new_exports(client):
    steps = [{"op": "sample_type", "values": ["Primary Tumor", "Solid Tissue Normal", "Metastatic"]},
             {"op": "one_per_patient", "rule": "priority"}]
    sid = _run(client, steps, "one sample per patient")
    res = client.get(f"/api/searches/{sid}").json()
    chooser = res["steps"][-1]
    assert res["final"]["samples"] == res["final"]["patients"]
    assert chooser["selection"]["patients"] >= 1 and chooser["selection"]["examples"][0]["kept"].endswith("-01")
    assert "sample_selection.csv" in res["files"] and "coverage.csv" in res["files"]
    sel = client.get(f"/api/searches/{sid}/files/sample_selection.csv")
    assert sel.status_code == 200 and "kept" in sel.text.splitlines()[0]
    assert client.get(f"/api/searches/{sid}/files/coverage.csv").status_code == 200
    # a cohort saved by an older version of the app ("prefer") still runs and edits
    p = client.post("/api/preview", json={"steps": [{**steps[0], "_cid": 1},
                                                  {"op": "one_per_patient", "prefer": ["Primary Tumor"], "_cid": 2}]}).json()
    assert p["ok"] and p["final"]["samples"] == res["final"]["samples"]


def test_compact_mutation_table_has_the_same_calls(tmp_path):
    from cohort_studio import ops
    from frontend.backend.service import compact_mutation
    data = build(tmp_path / "release")
    full, small = ops.load_mutation(data), compact_mutation(data)
    assert small.dtypes.unique().tolist() == ["int8"]
    assert list(small.index) == list(full.index) and list(small.columns) == list(full.columns)
    assert (small.astype("int64").to_numpy() == full.to_numpy()).all()
