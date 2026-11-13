"""Tests for the parser benchmark (evals/). No network: the model call is replaced by a fixed reply."""
import argparse
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

import harness  # noqa: E402
import run_eval  # noqa: E402
from cohort_studio.assistant import AIError, LLMClient  # noqa: E402
from cohort_studio.assistant.prompt import RULES  # noqa: E402
from cohort_studio.data import DataStore  # noqa: E402
from tests.make_fixture import build  # noqa: E402

RAW = json.loads(harness.CASES_FILE.read_text(encoding="utf-8"))["cases"]
CASES = harness.load_cases()
REAL = ROOT / "data" / "tcga-brca"
PT = {"op": "sample_type", "values": ["Primary Tumor"]}


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    return DataStore(build(tmp_path_factory.mktemp("release")))


def ai_args(**kw):
    return argparse.Namespace(**{"provider": "openai", "model": "fake", "base_url": "http://localhost:1/v1", **kw})


# ---------- the cases file is well formed ----------
def test_cases_are_well_formed():
    ids = [c["id"] for c in RAW]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    by_id = {c["id"]: c for c in RAW}
    for c in RAW:
        assert c.get("split") in ("dev", "holdout"), c["id"]
        assert c["request"].strip() and c.get("why"), c["id"]
        assert ("gold_steps" in c) != ("gold_from" in c), f"{c['id']}: give gold_steps or gold_from, not both"
        if "gold_from" in c:
            assert "gold_steps" in by_id[c["gold_from"]], f"{c['id']}: gold_from must point at a case with gold_steps"
        assert harness.read_expected(c["id"]) is not None, f"{c['id']}: run python evals/build_expected.py"


def test_paraphrases_share_their_source_answer():
    for c in CASES:
        src = next((r for r in RAW if r["id"] == c["id"]), {})
        if "gold_from" in src:
            assert c["gold_steps"] == next(r for r in RAW if r["id"] == src["gold_from"])["gold_steps"]


def test_prompt_examples_do_not_leak_benchmark_requests():
    """A benchmark request copied into the AI's instructions would make its score meaningless."""
    rules = RULES.lower()
    for case in RAW:
        assert case["request"].lower() not in rules, case["id"]


# ---------- scoring ----------
def test_score_is_a_set_comparison():
    s = harness.score(["a", "b", "b"], ["b", "a"])
    assert s["exact"] and s["jaccard"] == 1.0
    s = harness.score(["a", "c"], ["a", "b"])
    assert not s["exact"] and s["missing"] == 1 and s["extra"] == 1 and s["jaccard"] == pytest.approx(1 / 3)
    assert harness.score([], [])["exact"]


# ---------- the AI route as the benchmark calls it ----------
def test_run_ai_scores_the_assistant_draft(monkeypatch, store):
    reply = {"interpretation": "primary tumours", "steps": [PT], "mappings": [], "corrections": [],
             "clarifications": [], "unsupported": [], "assumptions": []}
    monkeypatch.setattr(LLMClient, "complete_json", lambda self, s, u: (json.loads(json.dumps(reply)), {}, False))
    out = run_eval.run_ai({"request": "primary tumours"}, store, ai_args())
    assert not out["fallback"] and not out["error"]
    assert out["spec_steps"][0] == PT
    assert len(out["ids"]) == 8


def test_run_ai_counts_an_unreachable_model_as_a_fallback(monkeypatch, store):
    def boom(self, s, u):
        raise AIError("Couldn't reach the model (connection refused).")
    monkeypatch.setattr(LLMClient, "complete_json", boom)
    out = run_eval.run_ai({"request": "primary tumours"}, store, ai_args())
    assert out["fallback"] and out["ids"] == [] and "connection refused" in out["error"]


def test_run_ai_survives_a_crash_in_the_ai_route(monkeypatch, store):
    """An unexpected exception (not AIError) costs that case, not the whole benchmark run."""
    def crash(*a, **kw):
        raise TypeError("'float' object is not subscriptable")
    monkeypatch.setattr("cohort_studio.assistant.make_draft", crash)
    out = run_eval.run_ai({"request": "primary tumours"}, store, ai_args())
    assert out["fallback"] and out["ids"] == [] and "crashed: TypeError" in out["error"]


# ---------- the answer key still reproduces from the gold steps (real data only) ----------
@pytest.mark.skipif(not (REAL / "raw" / "clinical.tsv").exists(), reason="run data/tcga-brca/fetch.py first")
def test_answer_key_reproduces_from_gold_steps():
    real = DataStore(REAL)
    for case in CASES:
        ids, error = harness.build_cohort(harness.gold_spec(case), real)
        assert not error, f"{case['id']}: {error}"
        assert set(ids) == set(harness.read_expected(case["id"])), case["id"]
