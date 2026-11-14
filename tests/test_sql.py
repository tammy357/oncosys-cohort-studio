"""The SQL view (cohort_studio/sql.py) must reproduce the engine exactly: every step's rows, samples and patients,
and the final sample IDs. Checked on the synthetic fixture for every kind of step, and on the real release's eval
cases when the raw files are downloaded (CI fetches them)."""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cohort_studio import engine, sql  # noqa: E402
from cohort_studio.data import DataStore  # noqa: E402
from tests.make_fixture import build  # noqa: E402

duckdb = pytest.importorskip("duckdb")

PT = {"op": "sample_type", "values": ["Primary Tumor"]}
ALL = {"op": "sample_type", "values": ["Primary Tumor", "Solid Tissue Normal", "Metastatic"]}
JC = {"op": "join", "table": "clinical", "key": "sample_id", "mode": "rows"}
JS = {"op": "join", "table": "survival", "key": "sample_id", "mode": "rows"}


def F(field, cmp, value, table="clinical"):
    return {"op": "filter", "table": table, "field": field, "cmp": cmp, "value": value}


SPECS = {
    "slides_rows_inflate": [PT, {"op": "join", "table": "slides", "key": "sample_id", "mode": "rows", "slide_kinds": ["diagnostic (FFPE)"]}],
    "slides_exists": [PT, {"op": "join", "table": "slides", "key": "sample_id", "mode": "exists", "slide_kinds": []}],
    "slides_then_dedupe": [PT, {"op": "join", "table": "slides", "key": "sample_id", "mode": "rows", "slide_kinds": []}, {"op": "dedupe"}],
    "clinical_filters": [PT, JC, F("er_status", "in", ["Positive"]), F("stage", "stage_in", ["II", "III"]), F("age", "between", [40, 65])],
    "not_in_and_notnull": [PT, JC, F("pam50", "not_in", ["LumA"]), F("er_status", "notnull", None)],
    "contains": [PT, JC, F("histology", "contains", ["lobular"])],
    "in_raw": [PT, JC, F("er_status", "in_raw", ["[Not Evaluated]"])],
    "survival": [PT, JS, F("os_time", "gte", 500, "survival"), F("pfi", "is_event", None, "survival")],
    "patient_key_inflates": [ALL, {"op": "join", "table": "clinical", "key": "patient_id", "mode": "rows"}],
    "layers": [PT, {"op": "layer", "layer": "rna"}, {"op": "exclude_layer", "layer": "protein"}],
    "mutation": [PT, {"op": "mutation", "gene": "TP53", "mutated": True}],
    "mutation_wt": [PT, {"op": "mutation", "gene": "PIK3CA", "mutated": False}],
    "any_of": [PT, JC, {"op": "any_of", "table": "clinical", "conditions": [{"field": "er_status", "cmp": "in", "value": ["Positive"]},
                                                                            {"field": "pam50", "cmp": "in", "value": ["Basal"]}]}],
    "pairs": [PT, {"op": "join", "table": "normals", "key": "patient_id", "mode": "rows", "add_to_cohort": True, "require_layers": ["rna"]}],
    "has_normal": [PT, {"op": "join", "table": "normals", "key": "patient_id", "mode": "exists"}],
    "one_per_patient": [ALL, {"op": "one_per_patient", "rule": "priority"}],
    "one_per_patient_normal_first": [ALL, {"op": "one_per_patient", "rule": "priority", "order": ["Solid Tissue Normal", "Primary Tumor", "Metastatic"]}],
    "where": [ALL, {"op": "where", "condition": {"any": [{"all": [{"layer": "protein"}, {"not": {"gene": "TP53", "mutated": True}}]},
                                                          {"table": "clinical", "field": "er_status", "cmp": "in", "value": ["Negative"]},
                                                          {"slide_kinds": ["frozen, top section"]}]}}],
    "where_both_of_pair": [PT, {"op": "join", "table": "normals", "key": "patient_id", "mode": "rows", "add_to_cohort": True},
                           {"op": "where", "apply_to": "both", "condition": {"all": [{"layer": "rna"}, {"sample_type": ["Primary Tumor", "Solid Tissue Normal"]}]}}],
}


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    return DataStore(build(tmp_path_factory.mktemp("release"), collection_days=True))


@pytest.mark.parametrize("name", sorted(SPECS))
def test_sql_matches_the_engine_on_the_fixture(store, name):
    spec = {"request": name, "steps": SPECS[name]}
    result = engine.run(spec, store)
    assert result.ok, [s.error for s in result.steps]
    v = sql.verify(sql.to_sql(spec, store), result)
    assert v["ok"], v


def test_earliest_sample_per_patient(store):
    spec = {"request": "earliest", "steps": [ALL, JC, {"op": "one_per_patient", "rule": "earliest"}]}
    result = engine.run(spec, store)
    v = sql.verify(sql.to_sql(spec, store), result)
    assert v["ok"], v


def test_the_script_names_every_step_and_its_lines(store):
    spec = {"request": "x", "steps": SPECS["clinical_filters"]}
    plan = sql.to_sql(spec, store)
    text = plan.text()
    assert "CREATE OR REPLACE MACRO is_missing" in text and "read_csv('data/" not in text.split("Sources")[0]
    lines = plan.line_map()
    assert sorted(lines) == list(range(len(spec["steps"])))
    rows = text.split("\n")
    for i, (a, b) in lines.items():
        assert rows[a - 1].startswith("s") and " AS (" in rows[a - 1] and a <= b


def test_a_difference_is_reported_not_hidden(store):
    spec = {"request": "x", "steps": SPECS["clinical_filters"]}
    other = engine.run({"request": "y", "steps": [PT]}, store)       # the wrong engine result on purpose
    v = sql.verify(sql.to_sql(spec, store), other)
    assert v["ok"] is False and v["reason"]


REAL = DataStore()
HAVE_REAL = REAL.available("clinical") and REAL.available("survival") and REAL.available("mutation")


@pytest.mark.skipif(not HAVE_REAL, reason="needs the real release: python data/tcga-brca/fetch.py --no-rna")
def test_sql_matches_the_engine_on_every_eval_case():
    sys.path.insert(0, str(ROOT / "evals"))
    from harness import gold_spec, load_cases
    bad = []
    for case in load_cases():
        spec = gold_spec(case)
        result = engine.run(spec, REAL)
        v = sql.verify(sql.to_sql(spec, REAL), result)
        if not v["ok"]:
            bad.append((case["id"], v.get("reason")))
    assert not bad, bad
