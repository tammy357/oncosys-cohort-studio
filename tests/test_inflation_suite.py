"""The join-inflation suite (evals/inflation_cases.json): the engine must find every inflating join, fix it so the
export has one row per sample, and flag nothing in the controls. Runs on the real release; skipped if not fetched."""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

import harness  # noqa: E402
from cohort_studio import engine, exports  # noqa: E402
from cohort_studio.data import DataStore  # noqa: E402

CASES = json.loads((ROOT / "evals" / "inflation_cases.json").read_text())["cases"]


@pytest.fixture(scope="module")
def store():
    s = DataStore(ROOT / "data" / "tcga-brca")
    if not s.available("clinical") or not s.available("survival"):
        pytest.skip("real release not fetched (python data/tcga-brca/fetch.py --no-rna)")
    return s


def test_every_case_has_a_frozen_expectation():
    assert all("expect" in c for c in CASES), "run python evals/run_inflation.py --freeze"
    assert {c["kind"] for c in CASES} >= {"slides", "clinical-by-patient", "survival-by-patient", "control"}


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_engine_flags_inflation_and_the_fix_leaves_one_row_per_sample(case, store):
    spec = {"request": case["request"], "steps": case["gold_steps"]}
    result = engine.run(spec, store)
    flagged = [s.index + 1 for s in result.steps if s.inflated]
    assert flagged == [s["step"] for s in case["expect"]["inflated_steps"]]
    if case["kind"] == "control":
        assert not flagged
    ids, err = harness.build_cohort(spec, store)
    assert not err
    assert len(ids) == len(set(ids)) == case["expect"]["samples_after_fix"]
    if flagged:
        fixed, _ = engine.apply_fixes(spec, result)
        after = engine.run(fixed, store)
        assert not any(s.inflated for s in after.steps)
        assert exports.cohort_frame(after)["sample_id"].is_unique
