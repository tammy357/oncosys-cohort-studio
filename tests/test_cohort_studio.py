"""Tests: run with  python -m pytest tests -q

Most tests use a tiny synthetic release (tests/make_fixture.py) so they run without downloading
anything. The last tests use the real committed manifests in data/tcga-brca.
"""
import json
import pathlib
import shutil
import subprocess
import sys

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cohort_studio import engine, exports, verdict  # noqa: E402
from cohort_studio.data import DataStore  # noqa: E402
from cohort_studio.parse import parse_request  # noqa: E402
from tests.make_fixture import build  # noqa: E402

REAL = ROOT / "data" / "tcga-brca"


@pytest.fixture(scope="module")
def fixture_dir(tmp_path_factory):
    return build(tmp_path_factory.mktemp("release"))


@pytest.fixture(scope="module")
def store(fixture_dir):
    return DataStore(fixture_dir)


def run_fixed(spec, store):
    r = engine.run(spec, store)
    if any(s.inflated for s in r.steps):
        spec, _ = engine.apply_fixes(spec, r)
        r = engine.run(spec, store)
    return r


def ids(*nums, suffix="01"):
    return [f"TCGA-ZZ-{n:04d}-{suffix}" for n in nums]


# ---------- joins and inflation ----------
def test_slide_join_inflates_then_fix(store):
    spec = parse_request("primary tumors with a diagnostic slide", store)
    r = engine.run(spec, store)
    slide_step = r.steps[-1]
    assert slide_step.inflated and slide_step.rows_out == 8 and slide_step.samples_out == 6
    assert any(ex["sample_id"] == "TCGA-ZZ-0001-01" and ex["rows"] == 2 for ex in slide_step.duplicate_examples)
    fixed = run_fixed(spec, store)
    assert fixed.cohort_ids == ids(1, 2, 4, 5, 6, 7)
    assert not fixed.unresolved_inflation


def test_clinical_join_on_patient_inflates(store):
    spec = {"request": "", "steps": [
        {"op": "sample_type", "values": ["Primary Tumor"]},
        {"op": "join", "table": "clinical", "key": "patient_id", "mode": "rows"}]}
    r = engine.run(spec, store)
    assert r.steps[1].inflated and r.steps[1].rows_out == 12 and r.steps[1].samples_out == 8
    new, changes = engine.apply_fixes(spec, r)
    assert new["steps"][1]["key"] == "sample_id"
    assert engine.run(new, store).final_rows == 8


def test_survival_join_on_patient_inflates(store):
    spec = parse_request("primary tumours with progression-free interval data joined by patient", store)
    r = engine.run(spec, store)
    assert any(s.inflated for s in r.steps)
    fixed = run_fixed(spec, store)
    assert fixed.cohort_ids == ids(1, 2, 3, 4, 5, 7, 8)   # patient 6 has no PFI


def test_unresolved_inflation_blocks(store):
    spec = parse_request("primary tumors with a diagnostic slide", store)
    r = engine.run(spec, store)
    v = verdict.evaluate(r, store)
    assert v.verdict == "Blocked"
    assert "counted twice" in v.reason


# ---------- filters ----------
@pytest.mark.parametrize("request_text,expected", [
    ("ER-positive primary tumours", ids(1, 2, 4, 7)),
    ("stage II-III tumours", ids(1, 3, 4, 8)),
    ("patients over 50", ids(2, 3, 4, 6, 7)),
    ("tumours with TP53 mutation", ids(3, 5, 8)),
    ("tumours without PIK3CA mutation", ids(2, 3, 5, 6, 8)),
    ("basal-like tumours", ids(3, 5)),
    ("luminal tumours with protein data", ids(1, 2, 4)),
    ("lobular tumours", ids(4)),
])
def test_filters(store, request_text, expected):
    r = run_fixed(parse_request(request_text, store), store)
    assert r.cohort_ids == expected, (request_text, r.cohort_ids)


def test_tumour_normal_pairs(store):
    r = run_fixed(parse_request("tumour-normal pairs", store), store)
    assert r.cohort_ids == sorted(ids(1, 2, 3) + ids(1, 2, 3, suffix="11"))


def test_triple_negative_is_blocked_for_size(store):
    r = run_fixed(parse_request("triple negative tumours", store), store)
    assert r.cohort_ids == ids(3, 5)
    assert verdict.evaluate(r, store).verdict == "Blocked"


@pytest.mark.parametrize("request_text", ["luminal A or B tumours", "luminal B and A tumours", "luminal A/B tumours",
                                          "luminal A, or B tumours", "luminal A and/or B tumours",
                                          "luminal A or luminal B tumours", "luminal tumours"])
def test_luminal_a_or_b_keeps_both(store, request_text):
    [f] = [s for s in parse_request(request_text, store)["steps"] if s.get("field") == "pam50"]
    assert f["value"] == ["LumA", "LumB"]


def test_single_luminal_subtype_still_works(store):
    [f] = [s for s in parse_request("luminal B tumours", store)["steps"] if s.get("field") == "pam50"]
    assert f["value"] == ["LumB"]


# ---------- verdict ----------
def test_unparsed_words_need_confirmation(store):
    spec = parse_request("primary tumours with purity above 60%", store)
    assert "purity" in spec["unparsed"]
    r = run_fixed(spec, store)
    assert verdict.evaluate(r, store).verdict in ("Insufficient Evidence", "Blocked")
    spec["unparsed_confirmed"] = True
    v = verdict.evaluate(run_fixed(spec, store), store)
    assert not any(c.status == "unknown" for c in v.checks)


def test_missing_file_is_insufficient_evidence(fixture_dir, tmp_path):
    copy = tmp_path / "release"
    shutil.copytree(fixture_dir, copy)
    (copy / "raw" / "clinical.tsv").unlink()
    s = DataStore(copy)
    r = engine.run(parse_request("ER-positive tumours", s), s)
    assert verdict.evaluate(r, s).verdict == "Insufficient Evidence"


def test_changed_file_is_insufficient_evidence(fixture_dir, tmp_path):
    copy = tmp_path / "release"
    shutil.copytree(fixture_dir, copy)
    with open(copy / "raw" / "clinical.tsv", "a") as f:
        f.write("\n")
    s = DataStore(copy)
    r = run_fixed(parse_request("ER-positive tumours", s), s)
    assert verdict.evaluate(r, s).verdict == "Insufficient Evidence"


# ---------- exports ----------
def test_reproduce_script_matches_app(store, fixture_dir, tmp_path):
    spec = parse_request("ER-positive tumours with a diagnostic slide and PIK3CA mutation", store)
    r = run_fixed(spec, store)
    assert r.cohort_ids == ids(1, 4)
    v = verdict.evaluate(r, store)
    out = exports.write_all(r, v, store, tmp_path / "out")
    subprocess.run([sys.executable, str(out / "reproduce_cohort.py"), str(fixture_dir), str(tmp_path / "again.csv")],
                   check=True, capture_output=True)
    assert (tmp_path / "again.csv").read_text() == (out / "cohort_samples.csv").read_text()
    cohort = pd.read_csv(out / "cohort_samples.csv")
    assert list(cohort.columns) == ["sample_id"] and cohort.sample_id.is_unique


def test_findings_json_is_valid(store, tmp_path):
    r = run_fixed(parse_request("primary tumors with a diagnostic slide", store), store)
    out = exports.write_all(r, verdict.evaluate(r, store), store, tmp_path / "out")
    res = subprocess.run([sys.executable, str(ROOT / "format" / "validate.py"), str(out / "findings.json")],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stdout
    doc = json.loads((out / "findings.json").read_text())
    assert any(f["kind"] == "join_inflation" for f in doc["findings"])


# ---------- one sample per patient ----------
def test_one_sample_per_patient_by_sample_type(store):
    spec = parse_request("all samples, one sample per patient", store)
    assert spec["steps"][-1] == {"op": "one_per_patient", "rule": "priority"}
    r = run_fixed(spec, store)
    assert r.cohort_ids == ids(1, 2, 3, 4, 5, 6, 7, 8)          # every patient keeps its primary tumour
    sel = exports.selection_frame(r)
    assert set(sel.patient_id) == {"TCGA-ZZ-0001", "TCGA-ZZ-0002", "TCGA-ZZ-0003", "TCGA-ZZ-0008"}
    assert sel.set_index("patient_id").loc["TCGA-ZZ-0008", "dropped"] == "TCGA-ZZ-0008-06"
    excl = exports.exclusions_frame(r)
    assert "TCGA-ZZ-0008-01" in excl.set_index("sample_id").loc["TCGA-ZZ-0008-06", "reason"]
    check = next(c for c in verdict.evaluate(r, store).checks if c.name == "One sample per patient")
    assert check.status == "pass" and "sample_selection.csv" in check.detail


def test_custom_order_keeps_metastasis(store):
    spec = {"request": "", "steps": [{"op": "sample_type", "values": ["Primary Tumor", "Metastatic"]},
                                     {"op": "one_per_patient", "rule": "priority",
                                      "order": ["Metastatic", "Primary Tumor"]}]}
    r = engine.run(spec, store)
    assert "TCGA-ZZ-0008-06" in r.cohort_ids and "TCGA-ZZ-0008-01" not in r.cohort_ids


def test_earliest_without_collection_dates_is_insufficient_evidence(store):
    spec = parse_request("primary and metastatic tumours, earliest sample per patient", store)
    assert spec["steps"][-1]["rule"] == "earliest" and not spec["unparsed"]
    v = verdict.evaluate(engine.run(spec, store), store)
    assert v.verdict == "Insufficient Evidence" and "collection dates" in v.reason


def test_earliest_with_collection_dates(tmp_path):
    d = build(tmp_path / "release", collection_days=True)
    s = DataStore(d)
    spec = parse_request("primary and metastatic tumours, earliest sample per patient", s)
    r = engine.run(spec, s)
    assert "TCGA-ZZ-0008-06" in r.cohort_ids and "TCGA-ZZ-0008-01" not in r.cohort_ids
    check = next(c for c in verdict.evaluate(r, s).checks if c.name == "Sample collection days make sense")
    assert check.status == "warn" and "TCGA-ZZ-0008" in check.detail     # metastasis dated before the primary
    out = exports.write_all(r, verdict.evaluate(r, s), s, tmp_path / "out")
    subprocess.run([sys.executable, str(out / "reproduce_cohort.py"), str(d), str(tmp_path / "again.csv")],
                   check=True, capture_output=True)
    assert (tmp_path / "again.csv").read_text() == (out / "cohort_samples.csv").read_text()


# ---------- expected shifts and coverage ----------
def test_receptor_filter_shift_in_pam50_is_labelled_expected(store):
    r = run_fixed(parse_request("ER-positive tumours", store), store)
    er_step = next(s for s in r.steps if s.op == "filter")
    assert er_step.shift["PAM50 subtype"]["expected"]
    assert r.overall_shift["PAM50 subtype"]["expected"]
    assert "Submitting site" in er_step.shift and not er_step.shift["Submitting site"]["expected"]


def test_coverage_lists_every_variable(store):
    r = run_fixed(parse_request("primary tumors", store), store)
    cov = exports.coverage_frame(r, store)
    row = cov.set_index("column").loc["breast_carcinoma_estrogen_receptor_status"]
    assert row["filter name"] == "er_status" and row["recorded"] == 7     # patient 6 is '[Not Evaluated]'
    assert not any(c.startswith("_") for c in cov["column"])


# ---------- the real committed manifests ----------
def test_menopause_status(store):
    # in the fixture, patients over 50 are post-menopausal: 2, 3, 4, 6, 7
    assert sorted(run_fixed(parse_request("postmenopausal primary tumours", store), store).cohort_ids) == ids(2, 3, 4, 6, 7)
    assert sorted(run_fixed(parse_request("pre-menopausal primary tumours", store), store).cohort_ids) == ids(1, 5, 8)


@pytest.mark.skipif(not (REAL / "raw" / "clinical.tsv").exists(), reason="run data/tcga-brca/fetch.py first")
def test_menopause_excludes_indeterminate_on_real_data():
    """'Indeterminate (neither Pre or Postmenopausal)' contains both 'pre' and 'post' but is neither."""
    s = DataStore(REAL)
    status = s.table("clinical").set_index("sample_id")["menopause_status"]
    for word, start in (("postmenopausal", "Post ("), ("premenopausal", "Pre (")):
        cohort = run_fixed(parse_request(f"{word} primary tumours", s), s).cohort_ids
        labels = set(status.reindex(list(cohort)))
        assert len(labels) == 1 and labels.pop().startswith(start), word


@pytest.mark.skipif(not (REAL / "sample_manifest.csv").exists(), reason="real manifests not present")
def test_brief_demo_on_real_manifests():
    s = DataStore(REAL)
    spec = parse_request("primary tumors with both RNA and protein data and a diagnostic slide", s)
    r = engine.run(spec, s)
    counts = [(st.rows_out, st.samples_out) for st in r.steps]
    assert counts == [(1101, 1101), (1097, 1097), (742, 742), (776, 731)]
    fixed = run_fixed(spec, s)
    assert len(fixed.cohort_ids) == 731 and not fixed.unresolved_inflation


@pytest.mark.skipif(not (REAL / "sample_manifest.csv").exists(), reason="real manifests not present")
def test_any_slide_join_on_real_manifests():
    s = DataStore(REAL)
    r = engine.run(parse_request("primary tumours with a pathology slide", s), s)
    assert r.steps[-1].inflated and r.steps[-1].rows_out > 2 * r.steps[-1].samples_out


@pytest.mark.skipif(not (REAL / "sample_manifest.csv").exists(), reason="real manifests not present")
def test_one_sample_per_patient_on_real_manifests():
    s = DataStore(REAL)
    r = engine.run(parse_request("primary and metastatic tumors with RNA data, one sample per patient", s), s)
    assert len(r.cohort_ids) == r.final_patients == 1097
    sel = exports.selection_frame(r)
    assert len(sel) == 7 and set(sel["kept sample type"]) == {"Primary Tumor"}
