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

from cohort_studio import engine, exports, ops, verdict  # noqa: E402
from cohort_studio.spec import validate  # noqa: E402
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


# ---------- gene names as people write them, and a prompt that shows format, not content ----------
def test_gene_names_written_with_separators_or_suffixes(cat):
    assert cat.resolve_gene("tp-53") == "TP53"
    assert cat.resolve_gene("TP53_mutated") == "TP53"
    assert cat.resolve_gene("PIK3CA-mutant") == "PIK3CA"
    assert cat.resolve_gene("gata3") == "GATA3"
    assert cat.resolve_gene("TP53-123") is None            # a suffix that isn't a word doesn't count
    found = cat.gene_mentions("TP53-mutated tumours that are HER2-negative, PIK3CA wild-type")
    assert [(g["gene"], g["found"]) for g in found] == [("TP53", True), ("PIK3CA", True)]
    typo = cat.gene_mentions("tp35-mutant tumours")
    assert typo[0]["found"] is False and typo[0]["gene"] == "TP35" and typo[0]["closest"][0] == "TP53"


def test_a_gene_written_with_a_hyphen_counts_as_named():
    from cohort_studio.assistant.draft import _gene_named
    assert _gene_named("tp-53 mutant basal tumours", "TP53")
    assert _gene_named("cdh1-mutant lobular tumours", "CDH1")
    assert not _gene_named("one sample per patient with mutation data", "TP53")


def test_checker_reads_a_gene_written_with_a_suffix(store, cat):
    chk = Checker(store, cat)
    assert chk.check_steps([{"op": "mutation", "gene": "PIK3CA-mutant", "mutated": True}]) == [
        {"op": "mutation", "gene": "PIK3CA", "mutated": True}]
    assert chk.corrections and chk.corrections[0]["to"] == "PIK3CA"


def test_prompt_shows_step_formats_not_values_to_copy():
    from cohort_studio.assistant.prompt import RULES
    assert '"require_layers":["rna"]' not in RULES          # an example value the model copied into unrelated requests
    assert '"gene":"TP53"' not in RULES
    assert '"add_to_cohort":false' in RULES and '"add_to_cohort":true' in RULES   # both meanings of a normals join
    assert "all three (Primary Tumor, Solid Tissue Normal, Metastatic)" in RULES
    assert "notnull on that endpoint, not is_event" in RULES


def test_a_gene_written_as_a_filter_on_the_mutation_table_becomes_a_mutation_step(store, cat):
    chk = Checker(store, cat)
    steps = chk.check_steps([
        {"op": "filter", "table": "mutation", "field": "PIK3CA", "cmp": "in", "value": ["wild-type"]},
        {"op": "filter", "table": "mutations", "field": "TP53", "cmp": "in", "value": ["mutated"]}])
    assert steps == [{"op": "mutation", "gene": "PIK3CA", "mutated": False}, {"op": "mutation", "gene": "TP53", "mutated": True}]
    assert not chk.unsupported


def test_prompt_rules_for_endpoints_per_patient_and_her2():
    from cohort_studio.assistant.prompt import RULES
    assert "Naming an endpoint" in RULES                       # 'progression-free interval' is a name, not an event
    assert "only when the request asks for the earliest" in RULES
    assert "her2_immunohistochemistry_level_result only when the request names a score" in RULES


# ---------- the 'where' step: AND / OR / NOT across tables, layers, genes and slides ----------
def _er(store):
    clin = store.table("clinical").drop_duplicates("sample_id").set_index("sample_id")
    return clin[store.resolve_field("clinical", "er_status")]


def _tp53(store):
    return pd.to_numeric(store.table("mutation").loc["TP53"], errors="coerce")


def test_where_or_across_tables(store):
    pt = [s for s in store.manifest.loc[store.manifest.sample_type == "Primary Tumor", "sample_id"]]
    er, tp53 = _er(store), _tp53(store)
    expected = sorted(s for s in pt if er.get(s) == "Positive" or tp53.get(s, float("nan")) > 0)
    steps = [PT, {"op": "where", "condition": {"any": [
        {"table": "clinical", "field": "er_status", "cmp": "in", "value": ["Positive"]}, {"gene": "TP53", "mutated": True}]}}]
    assert cohort(store, steps) == expected
    both_layers = [PT, {"op": "where", "condition": {"any": [{"layer": "rna"}, {"layer": "protein"}]}}]
    m = store.manifest
    assert cohort(store, both_layers) == sorted(m.loc[(m.sample_type == "Primary Tumor") & (m.has_rna | m.has_protein), "sample_id"])


def test_where_not_is_three_valued(store):
    """NOT (ER+ AND TP53 mutated): a sample without mutation data is unknown for TP53, so ER+ samples without it
    stay unknown and are left out, while ER- samples are kept whatever their TP53 status."""
    pt = [s for s in store.manifest.loc[store.manifest.sample_type == "Primary Tumor", "sample_id"]]
    er, tp53 = _er(store), _tp53(store)

    def both(s):
        a = None if ops.is_missing(er.get(s)) else bool(er.get(s) == "Positive")
        b = None if pd.isna(tp53.get(s, float("nan"))) else bool(tp53.get(s) > 0)
        return False if (a is False or b is False) else (None if (a is None or b is None) else True)
    expected = sorted(s for s in pt if both(s) is False)
    steps = [PT, {"op": "where", "condition": {"not": {"all": [
        {"table": "clinical", "field": "er_status", "cmp": "in", "value": ["Positive"]}, {"gene": "TP53", "mutated": True}]}}}]
    assert cohort(store, steps) == expected
    r = engine.run({"request": "t", "steps": steps}, store)
    assert set(r.steps[1].removed_reasons.values()) <= {"doesn't meet the condition", "a value the condition needs isn't recorded"}


def test_where_on_both_samples_of_a_pair(store):
    pair = {"op": "join", "table": "normals", "key": "patient_id", "mode": "rows", "add_to_cohort": True}
    via_layers = cohort(store, [PT, {"op": "layer", "layer": "rna"}, {**pair, "require_layers": ["rna"]}])
    via_where = cohort(store, [PT, pair, {"op": "where", "condition": {"layer": "rna"}, "apply_to": "both"}])
    assert via_where == via_layers and via_where


def test_where_is_validated(store):
    def problems(step):
        return validate({"steps": [PT, step]}, store)
    assert problems({"op": "where", "condition": {"layer": "rna"}, "apply_to": "both"})          # no pairs join before it
    assert problems({"op": "where", "condition": {"table": "clinical", "field": "smoking", "cmp": "in", "value": ["yes"]}})
    assert problems({"op": "where", "condition": {"gene": "NOTAGENE"}})
    assert problems({"op": "where", "condition": {"any": []}})
    assert not problems({"op": "where", "condition": {"not": {"slide_kinds": []}}})


def test_checker_cleans_a_condition_tree(store, cat):
    chk = Checker(store, cat)
    out = chk.check_steps([{"op": "where", "condition": {"or": [
        {"table": "clinical", "field": "er_status", "cmp": "in", "value": ["negative"]},
        {"gene": "tp53"}, {"layer": "RNA-seq"}, {"not": {"slides": ["diagnostic"]}}]}}])
    assert out == [{"op": "where", "condition": {"any": [
        {"table": "clinical", "field": "er_status", "cmp": "in", "value": ["Negative"]},
        {"gene": "TP53", "mutated": True}, {"layer": "rna"}, {"not": {"slide_kinds": ["diagnostic (FFPE)"]}}]}}]
    # an 'any_of' that mixes the clinical and survival tables becomes a condition step instead of being rejected
    mixed = Checker(store, cat).check_steps([{"op": "any_of", "table": "clinical", "conditions": [
        {"field": "er_status", "cmp": "in", "value": ["Negative"]}, {"table": "survival", "field": "os", "cmp": "is_event"}]}])
    assert mixed[0]["op"] == "where" and len(mixed[0]["condition"]["any"]) == 2


def test_reproduce_script_runs_a_condition(store, fixture_dir, tmp_path):
    spec = {"request": "where", "steps": [PT, {"op": "where", "condition": {"any": [
        {"table": "clinical", "field": "er_status", "cmp": "in", "value": ["Negative"]},
        {"all": [{"gene": "TP53", "mutated": True}, {"not": {"layer": "protein"}}]}]}}]}
    r = engine.run(spec, store)
    assert r.ok and r.cohort_ids
    out = exports.write_all(r, verdict.evaluate(r, store), store, tmp_path / "out")
    subprocess.run([sys.executable, str(out / "reproduce_cohort.py"), str(fixture_dir), str(tmp_path / "again.csv")],
                   check=True, capture_output=True)
    assert (tmp_path / "again.csv").read_text() == (out / "cohort_samples.csv").read_text()


def test_joining_a_table_twice_runs(store):
    steps = [PT, {"op": "join", "table": "clinical", "key": "sample_id", "mode": "rows"},
             {"op": "join", "table": "clinical", "key": "patient_id", "mode": "rows"},
             F("er_status", "in", ["Positive"])]
    r = engine.run({"request": "twice", "steps": steps}, store)
    assert r.ok and not [s.error for s in r.steps if s.error]
    assert sorted(r.cohort_ids) == cohort(store, [PT, JC, F("er_status", "in", ["Positive"])])


@pytest.mark.skipif(not (REAL / "raw" / "clinical.tsv").exists(), reason="real release not fetched")
def test_where_matches_set_logic_on_the_real_release():
    s = DataStore(REAL)
    m, sl = s.manifest, s.slides
    pt = list(m.loc[m.sample_type == "Primary Tumor", "sample_id"])
    er, tp53 = _er(s), _tp53(s)
    expected = sorted(x for x in pt if er.get(x) == "Negative" or tp53.get(x, float("nan")) > 0)
    got = engine.run({"request": "t", "steps": [PT, {"op": "where", "condition": {"any": [
        {"table": "clinical", "field": "er_status", "cmp": "in", "value": ["Negative"]}, {"gene": "TP53", "mutated": True}]}}]}, s)
    assert sorted(got.cohort_ids) == expected
    frozen = set(sl.loc[sl.slide_kind.str.startswith("frozen"), "sample_id"])
    normal = m[m.sample_type == "Solid Tissue Normal"].set_index("patient_id")["sample_id"]
    pairs = [(t, normal[p]) for t, p in m.loc[m.sample_type == "Primary Tumor", ["sample_id", "patient_id"]].values
             if p in normal.index]
    expected = sorted(x for t, n in pairs if t in frozen and n in frozen for x in (t, n))
    got = engine.run({"request": "t", "steps": [PT, {"op": "join", "table": "normals", "key": "patient_id", "mode": "rows",
                                                      "add_to_cohort": True},
                                                 {"op": "where", "condition": {"slide_kinds": list(ops.FROZEN_KINDS)},
                                                  "apply_to": "both"}]}, s)
    assert sorted(got.cohort_ids) == expected


# ---------- regressions from the review of the 'where' step ----------
def test_malformed_conditions_are_rejected_and_never_crash_the_run(store):
    bad = [{"layer": "rna", "gene": "TP53"},                                   # two tests in one
           {"all": [{"layer": "rna"}], "not": {"layer": "protein"}},           # a group with something beside it
           {"sample_type": "Primary Tumor"},                                   # a string, not a list
           ["not", "a", "dict"]]
    for cond in bad:
        spec = {"request": "bad", "steps": [PT, {"op": "where", "condition": cond}]}
        assert validate(spec, store), cond
        r = engine.run(spec, store)                                            # describing it must not crash
        assert r.steps[1].skipped and r.steps[1].error


def test_where_reasons_name_the_paired_normal(store):
    pair = {"op": "join", "table": "normals", "key": "patient_id", "mode": "rows", "add_to_cohort": True}
    r = engine.run({"request": "t", "steps": [PT, pair, {"op": "where", "condition": {"layer": "protein"},
                                                         "apply_to": "added_normals"}]}, store)   # no normal has protein
    reasons = set(r.steps[2].removed_reasons.values())
    assert reasons and all("paired normal" in x for x in reasons)


def test_conditions_get_the_same_expected_shift_labels_as_filters(store):
    er_pos = {"table": "clinical", "field": "er_status", "cmp": "in", "value": ["Positive"]}
    via_filter = engine.run({"request": "t", "steps": [PT, JC, F("er_status", "in", ["Positive"])]}, store).steps[2].shift
    via_where = engine.run({"request": "t", "steps": [PT, {"op": "where", "condition": er_pos}]}, store).steps[1].shift
    assert via_filter.get("PAM50 subtype", {}).get("expected")
    assert via_where["PAM50 subtype"]["expected"] == via_filter["PAM50 subtype"]["expected"]


def test_part_of_a_condition_that_cannot_be_read_drops_it_loudly(store, cat):
    chk = Checker(store, cat)
    out = chk.check_steps([{"op": "where", "condition": {"any": [
        {"table": "clinical", "field": "er_status", "cmp": "in", "value": ["NoSuchValue"]}, {"layer": "rna"}]}}])
    assert out == [] and chk.unsupported                  # not silently narrowed to 'has RNA'


def test_or_across_tables_keeps_each_condition_on_its_table(store, cat):
    chk = Checker(store, cat)
    out = chk.check_steps([{"op": "any_of", "table": "clinical", "conditions": [
        {"table": None, "field": "er_status", "cmp": "in", "value": ["negative"]},
        {"table": "survival", "field": "os", "cmp": "is_event"}]}])
    tests = out[0]["condition"]["any"]
    assert [t["table"] for t in tests] == ["clinical", "survival"]
    assert sum(c["to"] == "Negative" for c in chk.corrections) == 1      # each value fixed once


def test_slash_separates_genes_in_a_request(cat):
    found = cat.gene_mentions("TP53/PIK3CA-mutant tumours")
    assert [(g["gene"], g["found"]) for g in found] == [("TP53", True), ("PIK3CA", True)]


# ---------- regressions from the second, independent review ----------
ER_NEG = {"table": "clinical", "field": "er_status", "cmp": "in", "value": ["Negative"]}
PAIRS = {"op": "join", "table": "normals", "key": "patient_id", "mode": "rows", "add_to_cohort": True}


def test_checker_rejects_tree_nodes_with_stray_or_extra_keys(store, cat):
    """A node is exactly one group or one test; nothing beside it is silently dropped."""
    for cond in ({"all": [{"layer": "rna"}], "any": [{"layer": "protein"}]},
                 {"layer": "rna", "gene": "TP53"},
                 {"layer": "protein", "present": False},          # would have been read as 'has protein'
                 {"not": [{"layer": "rna"}, {"layer": "protein"}]}):  # neither, or not both? ambiguous
        chk = Checker(store, cat)
        assert chk.check_steps([PT, {"op": "where", "condition": cond}]) == [PT] and chk.unsupported, cond
    one = Checker(store, cat).check_steps([{"op": "where", "condition": {"not": [{"layer": "rna"}]}}])
    assert one == [{"op": "where", "condition": {"not": {"layer": "rna"}}}]


def test_a_gene_filter_needs_an_explicit_call(store, cat):
    def read(**f):
        chk = Checker(store, cat)
        return chk.check_steps([{"op": "filter", "table": "mutation", "field": "TP53", **f}]), chk
    for f in ({"cmp": "notnull"}, {"cmp": "is_event"}, {"cmp": "in", "value": None}, {"cmp": "in", "value": ["none"]},
              {"cmp": "in", "value": ["mutated", "wild-type"]}):
        out, chk = read(**f)
        assert out == [] and chk.unsupported, f                  # never guessed as 'not mutated'
    assert read(cmp="not_equal", value=["mutated"])[0] == [{"op": "mutation", "gene": "TP53", "mutated": False}]
    assert read(cmp="none_of", value=["wild-type"])[0] == [{"op": "mutation", "gene": "TP53", "mutated": True}]


def test_an_unnamed_gene_inside_a_condition_becomes_a_question():
    from cohort_studio.assistant.draft import _sanity_questions
    where = {"op": "where", "condition": {"any": [ER_NEG, {"gene": "TP53", "mutated": True}]}}
    keep, questions = _sanity_questions("ER-negative tumours", [PT, where])
    assert keep == [PT] and len(questions) == 1
    options = questions[0]["options"]
    assert options[0]["steps"] == [{"op": "where", "condition": ER_NEG}] and options[1]["steps"] == [where]
    named = {"op": "where", "condition": {"any": [ER_NEG, {"gene": "TP53", "mutated": True}]}}
    assert _sanity_questions("ER-negative or TP53-mutant tumours", [PT, named]) == ([PT, named], [])


def test_a_later_join_on_the_same_table_keeps_the_pairs_normals(store):
    pairs = cohort(store, [PT, PAIRS])
    assert any(s.endswith("-11") for s in pairs)
    again = cohort(store, [PT, PAIRS, {"op": "join", "table": "normals", "key": "patient_id", "mode": "exists"}])
    assert again == pairs


def test_verdict_checks_see_conditions(store):
    tp53 = {"op": "where", "condition": {"gene": "TP53", "mutated": True}}
    r = engine.run({"request": "t", "steps": [PT, tp53]}, store)
    pt = store.manifest.loc[store.manifest.sample_type == "Primary Tumor", "sample_id"]
    no_calls = (~pt.isin(store.table("mutation").columns)).mean()
    assert r.steps[1].missing_fraction == pytest.approx(no_calls) and "TP53" in r.steps[1].column
    gap_check = next(c for c in verdict.evaluate(r, store).checks if c.name.startswith("Filter fields"))
    assert gap_check.status == ("warn" if no_calls >= verdict.MISSING_WARN else "pass")
    os_where = {"op": "where", "condition": {"any": [ER_NEG, {"table": "survival", "field": "os", "cmp": "is_event"}]}}
    v = verdict.evaluate(engine.run({"request": "t", "steps": [PT, os_where]}, store), store)
    assert any(c.name == "Survival endpoint suits breast cancer" for c in v.checks)
    assert any("ER, PR and HER2" in x for x in v.limitations)
    assert any("MC3" in x for x in verdict.evaluate(r, store).limitations)


def test_only_mutation_words_after_a_gene_make_a_mutation_call(store, cat):
    assert cat.read_gene("TP53_WT") == ("TP53", False)
    assert cat.read_gene("PIK3CA-wild-type") == ("PIK3CA", False)
    assert cat.read_gene("tp-53-mutant") == ("TP53", True)
    assert cat.read_gene("GATA3-amplified") == (None, None)       # copy number, not a mutation
    assert cat.resolve_gene("GATA3-loss") is None
    chk = Checker(store, cat)
    assert chk.check_steps([{"op": "mutation", "gene": "TP53_WT", "mutated": True}]) == [
        {"op": "mutation", "gene": "TP53", "mutated": False}]
    chk = Checker(store, cat)
    assert not [s for s in chk.check_steps([{"op": "mutation", "gene": "GATA3-amplified"}]) if s.get("op") == "mutation"]
    note = cat.gene_mentions("GATA3-amplified tumours")
    assert note[0]["gene"] == "GATA3" and note[0]["found"] and "copy number" in note[0]["note"]


def test_a_slide_field_inside_a_condition_is_reported_not_a_crash(store, cat):
    chk = Checker(store, cat)
    out = chk.check_steps([PT, {"op": "where", "condition": {"any": [
        ER_NEG, {"table": "slides", "field": "slide_kind", "cmp": "in", "value": ["diagnostic"]}]}}])
    assert out == [PT] and chk.unsupported
    chk = Checker(store, cat)
    assert chk.check_steps([F("slide_kind", "in", ["diagnostic"], table="slides")]) == [] and chk.unsupported


def test_a_step_the_checker_drops_is_named_in_the_verdict_never_blank(store, cat):
    """A dropped step becomes an 'unused words' entry; the verdict explains each one, so none may be blank."""
    chk = Checker(store, cat)
    chk.check_steps([PT, {"op": "filter", "table": "clinical", "cmp": "in", "value": ["x"]},     # no field
                     {"op": "where", "condition": {"any": [ER_NEG, {"table": "clinical", "cmp": "in", "value": ["x"]}]}}])
    phrases = [u["phrase"] for u in chk.unsupported]
    assert len(phrases) == 2 and all(p.strip() for p in phrases)
    spec = {"request": "t", "steps": [PT], "unparsed": phrases}
    check = next(c for c in verdict.evaluate(engine.run(spec, store), store).checks
                 if c.name == "The whole request was turned into steps")
    assert check.status == "unknown" and "NOT applied" in check.detail


def test_raw_conditions_with_wrong_types_are_rejected_not_misread(store):
    for cond in ({"gene": ["TP53"]}, {"layer": ["rna"]}, {"gene": "TP53", "mutated": "no"},
                 {"table": "clinical", "field": "er_status", "cmp": "in", "value": "Negative"},   # read letter by letter
                 {"table": "clinical", "field": "stage", "cmp": "stage_in", "value": "II"},       # read as I
                 {"table": "clinical", "field": "age", "cmp": "gte", "value": "50"},
                 {"table": "clinical", "field": "age", "cmp": "between", "value": [40]},
                 {"sample_type": [["Primary Tumor"]]}):
        spec = {"request": "raw", "steps": [PT, {"op": "where", "condition": cond}]}
        assert validate(spec, store), cond
        r = engine.run(spec, store)
        assert r.steps[1].skipped and r.steps[1].error
