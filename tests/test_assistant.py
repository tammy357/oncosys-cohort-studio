"""Tests for the grounded AI route (cohort_studio/assistant) and the steps it can use.

No network: a scripted stand-in model is used, and LLMClient's HTTP call is replaced to inspect the payload.
Most tests run on the synthetic fixture (tests/make_fixture.py); a few use the real release when it is fetched.
"""
import json
import pathlib
import subprocess
import sys

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cohort_studio import engine, exports, verdict  # noqa: E402
from cohort_studio.assistant import (AIError, Catalog, LLMClient, ScriptedClient, assemble, make_draft,  # noqa: E402
                                     materialize, run_cohort, to_spec)
from cohort_studio.assistant.client import parse_json  # noqa: E402
from cohort_studio.assistant.verify import Checker, canonicalize  # noqa: E402
from cohort_studio.data import DataStore  # noqa: E402
from tests.make_fixture import build  # noqa: E402

REAL = ROOT / "data" / "tcga-brca"
PT = {"op": "sample_type", "values": ["Primary Tumor"]}
JC = {"op": "join", "table": "clinical", "key": "sample_id", "mode": "rows"}


def F(field, cmp, value, table="clinical"):
    return {"op": "filter", "table": table, "field": field, "cmp": cmp, "value": value}


def ids(*nums, suffix="01"):
    return [f"TCGA-ZZ-{n:04d}-{suffix}" for n in nums]


@pytest.fixture(scope="module")
def fixture_dir(tmp_path_factory):
    return build(tmp_path_factory.mktemp("release"))


@pytest.fixture(scope="module")
def store(fixture_dir):
    return DataStore(fixture_dir)


@pytest.fixture(scope="module")
def cat(store):
    return Catalog.for_store(store)


def cohort(store, steps):
    found, r = run_cohort(store, "test", steps)
    assert found is not None, [s.error for s in r.steps]
    return sorted(found)


# ---------- the new steps ----------
def test_exclude_layer(store):
    assert cohort(store, [PT, {"op": "exclude_layer", "layer": "protein"}]) == ids(6, 7, 8)


def test_any_of_is_an_or(store):
    steps = [PT, JC, {"op": "any_of", "table": "clinical", "conditions": [
        {"field": "er_status", "cmp": "in", "value": ["Positive"]},
        {"field": "pr_status", "cmp": "in", "value": ["Positive"]}]}]
    assert cohort(store, steps) == ids(1, 2, 4, 6, 7, 8)


def test_one_per_patient_uses_the_engine_step(store):
    steps = [{"op": "sample_type", "values": ["Primary Tumor", "Solid Tissue Normal", "Metastatic"]},
             {"op": "one_per_patient", "rule": "priority"}]
    assert cohort(store, steps) == ids(*range(1, 9))


def test_checker_reads_per_patient_rules(store):
    cat = Catalog.for_store(store)
    chk = Checker(store, cat)
    # the older form saved by earlier versions of the web app is read as a priority order
    old = chk.check_steps([{"op": "one_per_patient", "prefer": ["Primary Tumor", "Metastatic", "Solid Tissue Normal"]}])
    assert old == [{"op": "one_per_patient", "rule": "priority"}]
    normal_first = chk.check_steps([{"op": "one_per_patient", "rule": "priority", "order": ["normal"]}])
    assert normal_first[0]["order"][0] == "Solid Tissue Normal"
    # this fixture has no collection days, so "earliest" is reported as not possible, never guessed
    chk2 = Checker(store, cat)
    assert chk2.check_steps([{"op": "one_per_patient", "rule": "first"}]) == []
    assert "collection days" in chk2.unsupported[0]["reason"]


def test_earliest_sample_gets_its_clinical_join(tmp_path):
    days = DataStore(build(tmp_path / "days", collection_days=True))
    chk = Checker(days, Catalog.for_store(days))
    steps, _ = canonicalize(chk.check_steps([{"op": "one_per_patient", "rule": "earliest"}]))
    assert [s["op"] for s in steps] == ["sample_type", "join", "one_per_patient"]
    found, r = run_cohort(days, "earliest", steps)
    assert found and r.ok


def test_normals_must_have_the_layer(store):
    pair = {"op": "join", "table": "normals", "key": "patient_id", "mode": "rows", "add_to_cohort": True}
    assert cohort(store, [PT, {**pair, "require_layers": ["rna"]}]) == sorted(ids(1, 2, 3) + ids(1, 2, 3, suffix="11"))
    found, r = run_cohort(store, "t", [PT, {**pair, "require_layers": ["protein"]}])
    assert found == set()


def test_reproduce_script_covers_the_new_steps(store, fixture_dir, tmp_path):
    spec = {"request": "new steps", "steps": [
        {"op": "sample_type", "values": ["Primary Tumor", "Solid Tissue Normal"]},
        {"op": "exclude_layer", "layer": "mutation"},
        JC, {"op": "any_of", "table": "clinical", "conditions": [
            {"field": "er_status", "cmp": "in", "value": ["Positive"]},
            {"field": "pam50", "cmp": "in", "value": ["Basal"]}]},
        {"op": "one_per_patient", "rule": "priority", "order": ["Primary Tumor", "Metastatic", "Solid Tissue Normal"]}]}
    r = engine.run(spec, store)
    assert r.ok and r.cohort_ids
    out = exports.write_all(r, verdict.evaluate(r, store), store, tmp_path / "out")
    subprocess.run([sys.executable, str(out / "reproduce_cohort.py"), str(fixture_dir), str(tmp_path / "again.csv")],
                   check=True, capture_output=True)
    assert (tmp_path / "again.csv").read_text() == (out / "cohort_samples.csv").read_text()


# ---------- checking AI steps against the release ----------
def test_small_fixes_are_made_and_recorded(store, cat):
    chk = Checker(store, cat)
    steps = chk.check_steps([
        {"op": "sample_type", "values": ["tumour"]},
        {"op": "filter", "table": "clinical", "field": "estrogen_receptor_status", "cmp": "==", "value": "positive"},
        {"op": "filter", "table": "clinical", "field": "menopause", "cmp": "contains", "value": ["post"]},
        {"op": "layer", "layer": "RNA-seq"}])
    assert steps[0] == PT
    assert steps[1] == F("er_status", "in", ["Positive"])
    assert steps[2]["cmp"] == "in" and steps[2]["value"] == ["Post (prior bilateral ovariectomy)"]
    assert steps[3] == {"op": "layer", "layer": "rna"}
    assert not chk.unsupported and len(chk.corrections) >= 4


def test_sub_stages_become_exact_values(store, cat):
    chk = Checker(store, cat)
    [step] = chk.check_steps([F("stage", "stage_in", ["IIA", "IIB"])])
    assert step["cmp"] == "in" and sorted(step["value"]) == ["Stage IIA", "Stage IIB"]
    assert cohort(store, [PT, JC, step]) == ids(1, 4)


def test_invented_field_is_unsupported_not_guessed(store, cat):
    chk = Checker(store, cat)
    assert chk.check_steps([F("smoking_history", "notnull", None)]) == []
    assert chk.unsupported and "smoking_history" in chk.unsupported[0]["reason"]


def test_unknown_gene_becomes_a_question(store, cat):
    chk = Checker(store, cat)
    assert chk.check_steps([{"op": "mutation", "gene": "TP35", "mutated": True}]) == []
    [q] = chk.questions
    assert q["options"][0]["steps"] == [{"op": "mutation", "gene": "TP53", "mutated": True}]


def test_years_or_days_is_asked(store, cat):
    chk = Checker(store, cat)
    assert chk.check_steps([F("os_time", "gte", 5, table="survival")]) == []
    [q] = chk.questions
    assert q["options"][0]["steps"][0]["value"] == 1826 and q["options"][1]["steps"][0]["value"] == 5


def test_canonical_order_adds_sample_type_and_joins():
    steps, notes = canonicalize([F("er_status", "in", ["Positive"]), {"op": "layer", "layer": "rna"}])
    assert [s["op"] for s in steps] == ["sample_type", "layer", "join", "filter"] and notes


# ---------- drafts: questions, counts, auto-resolution, coverage ----------
HR_REPLY = {"interpretation": "HR+ tumours", "steps": [PT], "mappings": [{"phrase": "tumours", "meaning": "primary tumours"}],
            "clarifications": [{"id": "q1", "phrase": "hormone receptor positive", "question": "Which definition?",
                                "options": [
                                    {"label": "ER+ or PR+", "steps": [{"op": "any_of", "table": "clinical", "conditions": [
                                        {"field": "er_status", "cmp": "in", "value": ["Positive"]},
                                        {"field": "pr_status", "cmp": "in", "value": ["Positive"]}]}]},
                                    {"label": "ER+ and PR+", "steps": [F("er_status", "in", ["Positive"]),
                                                                       F("pr_status", "in", ["Positive"])]}]}]}


def test_questions_carry_real_counts_and_answers_build_the_spec(store, cat):
    d = assemble("hormone receptor positive tumours", HR_REPLY, store, cat)
    assert d["status"] == "needs_answers"
    assert [o["n_samples"] for o in d["clarifications"][0]["options"]] == [6, 4]
    spec = to_spec(d, {"q1": 1})
    assert sorted(engine.run(spec, store).cohort_ids) == ids(1, 2, 4, 7)
    assert not spec["unparsed"]


def test_unanswered_question_needs_confirmation(store, cat):
    d = assemble("hormone receptor positive tumours", HR_REPLY, store, cat)
    spec = to_spec(d)
    assert "Which definition?" in spec["unparsed"]
    r = engine.run(spec, store)
    assert verdict.evaluate(r, store).verdict in ("Insufficient Evidence", "Blocked")


def test_question_that_doesnt_change_the_cohort_is_not_asked(store, cat):
    reply = {"steps": [PT], "clarifications": [{"id": "q1", "question": "Stage I by group or exact?", "options": [
        {"label": "group", "steps": [F("stage", "stage_in", ["IV"])]},
        {"label": "exact", "steps": [F("stage", "in", ["Stage IV"])]}]}]}
    d = assemble("stage IV tumours", reply, store, cat)
    assert d["status"] == "ready" and not d["clarifications"] and d["auto_resolved"]
    assert cohort(store, materialize(d)) == ids(5)


def test_unaccounted_negation_is_flagged(store, cat):
    d = assemble("tumours that are not basal", {"steps": [PT, JC, F("pam50", "in", ["Basal"])],
                                                 "mappings": [{"phrase": "basal", "meaning": "PAM50 Basal"}]}, store, cat)
    assert d["uncovered"] == ["not"] and "not" in to_spec(d)["unparsed"]
    ok = assemble("tumours that are not basal", {"steps": [PT, JC, F("pam50", "not_in", ["Basal"])],
                                                  "mappings": [{"phrase": "not basal", "meaning": "PAM50 not Basal"}]}, store, cat)
    assert ok["uncovered"] == []


def test_unsupported_request_cannot_be_answered(store, cat):
    d = assemble("smokers", {"steps": [PT], "unsupported": [{"phrase": "smokers", "reason": "no smoking field"}]}, store, cat)
    assert d["status"] == "cannot_answer"


def test_gene_the_user_never_named_becomes_a_question(store, cat):
    d = assemble("tumours with mutation data", {"steps": [PT, {"op": "mutation", "gene": "PIK3CA", "mutated": True}]},
                 store, cat)
    [q] = d["clarifications"]
    assert q["options"][0]["steps"] == [{"op": "layer", "layer": "mutation"}]
    assert [o["n_samples"] for o in q["options"]] == [7, 2]
    assert cohort(store, materialize(d)) == ids(1, 2, 3, 4, 5, 6, 8)   # recommended: any sample with mutation data
    named = assemble("PIK3CA-mutated tumours", {"steps": [PT, {"op": "mutation", "gene": "PIK3CA", "mutated": True}]}, store, cat)
    assert not named["clarifications"]


def test_unnamed_survival_clock_is_asked(store, cat):
    reply = {"steps": [PT, {"op": "join", "table": "survival", "key": "sample_id", "mode": "rows"},
                       F("pfi_time", "gte", 600, table="survival")]}
    d = assemble("tumours with at least 600 days of follow-up", reply, store, cat)
    [q] = d["clarifications"]
    assert [o["steps"][0]["field"] for o in q["options"]] == ["os_time", "pfi_time"]
    assert not assemble("tumours with 600 days of progression-free follow-up", reply, store, cat)["clarifications"]


def test_make_draft_with_a_scripted_model(store, cat):
    client = ScriptedClient({"ER-positive tumours": {"steps": [PT, JC, F("er_status", "in", ["positive"])],
                                                     "mappings": [{"phrase": "ER-positive", "meaning": "ER Positive"}]}})
    d = make_draft("ER-positive tumours", store, client, catalog=cat)
    assert d["status"] == "ready" and cohort(store, materialize(d)) == ids(1, 2, 4, 7)
    assert any(c["to"] == "Positive" for c in d["corrections"])


# ---------- the model client ----------
def test_parse_json_tolerates_fences_and_prose():
    assert parse_json('```json\n{"steps": []}\n```') == {"steps": []}
    assert parse_json('Here you go: {"a": 1} hope it helps') == {"a": 1}
    with pytest.raises(AIError):
        parse_json("no json here")


class _MemCache:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def set(self, k, text, usage):
        self.d[k] = (text, usage)


@pytest.mark.parametrize("model,reasoning", [("gpt-4.1-mini", False), ("gpt-5-mini", True)])
def test_openai_payload_suits_the_model_and_cache_avoids_repeat_calls(monkeypatch, model, reasoning):
    sent = []

    def fake_post(self, url, payload, headers):
        sent.append(payload)
        return {"choices": [{"message": {"content": '{"steps": []}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    monkeypatch.setattr(LLMClient, "_post", fake_post)
    client = LLMClient(provider="openai", model=model, api_key="test-key-not-real", cache=_MemCache())
    assert client.complete_json("system", "user")[2] is False
    assert client.complete_json("system", "user")[2] is True        # second call served from the cache
    assert len(sent) == 1
    p = sent[0]
    assert p["response_format"] == {"type": "json_object"}
    if reasoning:
        assert "temperature" not in p and "max_completion_tokens" in p and "max_tokens" not in p
    else:
        assert p["temperature"] == 0 and "max_tokens" in p


def test_streamlit_and_cli_ai_path_uses_the_assistant(monkeypatch, store):
    """llm.ai_parse (used by app.py and the CLI) can't ask back: it uses the recommended answer and says so."""
    from cohort_studio import llm
    monkeypatch.setattr(LLMClient, "complete_json", lambda self, s, u: (json.loads(json.dumps(HR_REPLY)), {}, False))
    spec, problems = llm.ai_parse("hormone receptor positive tumours", store, provider="openai", api_key="not-real")
    assert not problems and spec["parser"].startswith("ai:")
    assert any("The AI has a question" in n for n in spec["notes"]) and "Which definition?" in spec["unparsed"]
    assert sorted(engine.run(spec, store).cohort_ids) == ids(1, 2, 4, 6, 7, 8)


def test_client_without_key_refuses_before_calling(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    with pytest.raises(AIError):
        LLMClient(provider="openai", model="gpt-4.1-mini").complete_json("s", "u")


# ---------- the real release (when fetched) ----------
needs_real = pytest.mark.skipif(not (REAL / "raw" / "clinical.tsv").exists(), reason="run data/tcga-brca/fetch.py first")


@needs_real
def test_real_release_grounding():
    s = DataStore(REAL)
    c = Catalog.for_store(s)
    chk = Checker(s, c)
    [step] = chk.check_steps([F("her2_status", "in", ["equivocal"])])
    assert step["cmp"] == "in_raw" and step["value"] == ["Equivocal"]
    assert len(cohort(s, [PT, JC, step])) == 179
    [fixed] = chk.check_steps([F("neoadjuvant_treatment", "in", ["No"])])
    assert fixed["field"] == "history_of_neoadjuvant_treatment"
    assert c.closest_genes("TP35")[0] == "TP53"
    assert "pathologic_N" in c.relevant_fields("lymph node positive tumours")
    assert "person_neoplasm_cancer_status" not in c.relevant_fields("patients whose cancer progressed")
    assert c.missing_concepts("smokers")
    d = assemble("patients whose cancer progressed, with RNA data",
                 {"steps": [PT, {"op": "layer", "layer": "rna"}, JC,
                            F("person_neoplasm_cancer_status", "in", ["WITH TUMOR"])]}, s, c)
    [q] = d["clarifications"]
    assert q["options"][0]["n_samples"] == 145 and q["options"][1]["n_samples"] == 126
