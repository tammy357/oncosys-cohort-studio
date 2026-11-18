"""Changes made after the judges' feedback (OncoSysHack, Nov 2026): intended use, named sign-off, the permutation
test in the shift rule, a finding for every check that doesn't pass, and recorded package versions."""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cohort_studio import api, engine, exports, verdict  # noqa: E402
from cohort_studio.data import DataStore  # noqa: E402
from cohort_studio.parse import parse_request  # noqa: E402
from tests.make_fixture import build  # noqa: E402


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    return DataStore(build(tmp_path_factory.mktemp("release")))


def check(v, name):
    return next((c for c in v.checks if c.name == name), None)


# ---------- 1. the stated outcome changes which checks apply ----------
def test_outcome_adds_a_coverage_check(store, tmp_path):
    run = api.build_cohort("primary tumours", store=store, ai="off", outcome="pfi", out_dir=tmp_path)
    c = check(run.verdict, "The outcome you named is recorded for the cohort")
    assert c is not None and "recorded for" in c.detail and "events" in c.detail
    assert run.spec["intended_use"]["outcome"] == "pfi"


def test_no_outcome_no_check_but_a_limitation(store, tmp_path):
    run = api.build_cohort("primary tumours", store=store, ai="off", out_dir=tmp_path)
    assert check(run.verdict, "The outcome you named is recorded for the cohort") is None
    assert any("No outcome was named" in x for x in run.verdict.limitations)


def test_unknown_outcome_is_insufficient_evidence(store, tmp_path):
    run = api.build_cohort("primary tumours", store=store, ai="off", outcome="no_such_field", out_dir=tmp_path)
    assert check(run.verdict, "The outcome you named is recorded for the cohort").status == "unknown"
    assert run.verdict.verdict in ("Insufficient Evidence", "Blocked")


def test_os_outcome_raises_the_endpoint_warning(store, tmp_path):
    run = api.build_cohort("primary tumours", store=store, ai="off", outcome="os", out_dir=tmp_path)
    assert check(run.verdict, "Survival endpoint suits breast cancer") is not None


# ---------- 2. a confirmation names who made it ----------
def test_confirmation_needs_a_name(store, tmp_path):
    with pytest.raises(ValueError):
        api.build_cohort("primary tumours with purity above 60%", store=store, ai="off", accept_unparsed=True,
                         out_dir=tmp_path)


def test_confirmation_is_recorded_and_quoted(store, tmp_path):
    run = api.build_cohort("primary tumours with purity above 60%", store=store, ai="off", accept_unparsed=True,
                           reviewer="A. Reviewer", reviewer_role="statistician", review_note="purity isn't in this release",
                           out_dir=tmp_path)
    assert run.spec["sign_off"]["name"] == "A. Reviewer"
    c = check(run.verdict, "The whole request was turned into steps")
    assert c.status == "pass" and "Confirmed by A. Reviewer (statistician)" in c.detail
    saved = json.loads((tmp_path / "cohort_spec.json").read_text())
    assert saved["sign_off"]["note"] == "purity isn't in this release"


def test_cli_refuses_accept_without_reviewer():
    from cohort_studio.__main__ import main
    with pytest.raises(SystemExit) as exc:
        main(["primary tumours", "--rules", "--accept-unparsed"])
    assert exc.value.code == 2


# ---------- 3. the permutation test is part of the shift rule ----------
@pytest.mark.parametrize("info, flagged", [
    ({"tvd": 0.05, "p_value": 0.001}, False),     # small shift: never flagged
    ({"tvd": 0.15, "p_value": 0.001}, True),      # large and beyond chance
    ({"tvd": 0.15, "p_value": 0.30}, False),      # large, but random dropout does this often
    ({"tvd": 0.15}, True),                        # test couldn't run: TVD alone decides
])
def test_shift_rule_uses_the_permutation_test(info, flagged):
    assert verdict.is_flagged_shift(info) is flagged


# ---------- 4. every check that doesn't pass has a finding ----------
def test_findings_cover_every_non_passing_check(store):
    spec = parse_request("primary tumours with purity above 60%", store)
    r = engine.run(spec, store)
    v = verdict.evaluate(r, store)
    doc = exports.findings_document(r, v, store)
    open_checks = [c for c in v.checks if c.status != "pass"]
    assert open_checks
    for c in open_checks:
        assert any(verdict.PROBLEM_HEADLINES.get(c.name, c.name) in f["summary"] or f["kind"] == "join_inflation"
                   for f in doc["findings"]), c.name


def test_every_check_name_has_a_headline():
    import inspect
    import re
    names = set(re.findall(r'Check\("([^"]+)"', inspect.getsource(verdict)))
    names |= {"The outcome you named is recorded for the cohort"}
    missing = names - set(verdict.PROBLEM_HEADLINES)
    assert not missing, missing


# ---------- 6. versions are recorded with the cohort ----------
def test_versions_are_recorded(store, tmp_path):
    run = api.build_cohort("primary tumours", store=store, ai="off", out_dir=tmp_path)
    env = json.loads((tmp_path / "cohort_spec.json").read_text())["environment"]
    assert env["python"] and env["pandas"]


# ---------- Luke's feedback: analytical intent with groups, an attached plan, and a raw data view ----------
def test_compare_by_adds_group_check_and_plan(store, tmp_path):
    run = api.build_cohort("primary tumours", store=store, ai="off", outcome="pfi", compare_by="pam50",
                           out_dir=tmp_path)
    c = check(run.verdict, "Each group you compare is large enough")
    assert c is not None and "events" in c.detail
    plan = json.loads((tmp_path / "analysis_plan.json").read_text())
    assert plan["analysis"] == "survival" and plan["time"]["column"] == "PFI.time"
    assert (tmp_path / "analysis_starter.py").exists()


def test_starter_script_runs(store, tmp_path):
    import subprocess
    api.build_cohort("primary tumours", store=store, ai="off", outcome="pfi", compare_by="pam50", out_dir=tmp_path)
    done = subprocess.run([sys.executable, str(tmp_path / "analysis_starter.py")], cwd=ROOT, capture_output=True,
                          text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert "median time" in done.stdout


def test_unknown_compare_field_is_insufficient(store, tmp_path):
    run = api.build_cohort("primary tumours", store=store, ai="off", compare_by="no_such_field", out_dir=tmp_path)
    assert check(run.verdict, "Each group you compare is large enough").status == "unknown"


def test_no_intent_no_plan(store, tmp_path):
    api.build_cohort("primary tumours", store=store, ai="off", out_dir=tmp_path)
    assert not (tmp_path / "analysis_plan.json").exists()


def test_raw_view_whole_run_and_one_step(store):
    run = api.build_cohort("primary tumours with ER positive", store=store, ai="off", write=False, outcome="pfi")
    whole = exports.sample_view_frame(run.result, store)
    assert set(whole["status"]) >= {"in cohort", "removed"}
    assert "PFI" in whole.columns and "Submitting site" in whole.columns
    step2 = exports.sample_view_frame(run.result, store, step=2)
    removed = step2[step2["status"] == "removed at step 2"]
    assert len(step2) == run.result.steps[1].samples_in
    assert len(removed) == len(run.result.steps[1].removed)
    assert (removed["reason"] != "").all()
