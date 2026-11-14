"""The visual canvas's API (frontend/backend/canvas.py), on the synthetic fixture: no download, key or network."""
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["COHORT_STUDIO_NO_DOTENV"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from cohort_studio import sql  # noqa: E402
from frontend.backend.config import Settings  # noqa: E402
from frontend.server import create_app  # noqa: E402
from tests.make_fixture import build  # noqa: E402

PT = {"op": "sample_type", "values": ["Primary Tumor"], "_cid": 1}
SLIDES = {"op": "join", "table": "slides", "key": "sample_id", "mode": "rows", "slide_kinds": ["diagnostic (FFPE)"], "_cid": 2}


@pytest.fixture()
def client(tmp_path):
    data = build(tmp_path / "release")
    settings = Settings(data_dir=str(data), database_url=f"sqlite:///{(tmp_path / 'app.db').as_posix()}", ai_enabled=False)
    return TestClient(create_app(settings))


def test_page_and_catalog(client):
    assert client.get("/canvas").status_code == 200
    cat = client.get("/api/canvas/catalog").json()
    tables = {t["id"]: t for t in cat["tables"]}
    assert {"samples", "slides", "clinical", "survival", "normals"} <= set(tables)
    assert tables["slides"]["grain"] == "slide" and tables["slides"]["per_sample"]["max"] == 3
    assert tables["samples"]["per_patient"]["max"] == 2           # patient 8: tumour + metastasis; 1-3: tumour + normal
    assert cat["templates"] and all(t["steps"] for t in cat["templates"])
    assert cat["not_in_release"] and cat["sql"]["available"] == sql.available()


def test_evaluate_marks_grain_inflation_and_the_fix(client):
    ev = client.post("/api/canvas/evaluate", json={"steps": [PT, SLIDES]}).json()
    assert ev["ok"] and [s["cid"] for s in ev["steps"]] == [1, 2]
    pt, join = ev["steps"]
    assert pt["grain"] == "patient" and join["grain"] == "multi" and join["inflated"]
    assert join["fix_step"]["mode"] == "exists" and join["duplicate_examples"]
    assert ev["final"]["rows"] > ev["final"]["samples"] and not ev["exportable"]
    assert set(ev["cohort_ids"]) and ev["verdict"]["verdict"] == "Blocked"
    fixed = client.post("/api/canvas/evaluate", json={"steps": [PT, {**SLIDES, **join["fix_step"], "_cid": 2}]}).json()
    assert fixed["exportable"] and fixed["final"]["rows"] == fixed["final"]["samples"]


def test_a_filter_gets_the_join_it_needs(client):
    steps = [PT, {"op": "filter", "table": "clinical", "field": "er_status", "cmp": "in", "value": ["positive"], "_cid": 2}]
    ev = client.post("/api/canvas/evaluate", json={"steps": steps}).json()
    run = ev["run_steps"]
    assert [s["op"] for s in run] == ["sample_type", "join", "filter"] and run[1]["_cid"] is None
    assert run[2]["value"] == ["Positive"] and ev["fixes"]


def test_switched_off_and_unknown_steps(client):
    off = client.post("/api/canvas/evaluate", json={"steps": [PT, {**SLIDES, "_off": True}]}).json()
    assert [s["cid"] for s in off["steps"]] == [1] and off["exportable"]
    bad = {"op": "filter", "table": "clinical", "field": "smoking", "cmp": "in", "value": ["yes"], "_cid": 3}
    ev = client.post("/api/canvas/evaluate", json={"steps": [PT, bad]}).json()
    assert any(p["cid"] == 3 for p in ev["problems"])
    empty = client.post("/api/canvas/evaluate", json={"steps": []}).json()
    assert empty["empty"] and empty["final"]["samples"] == empty["start"]["samples"]


def test_code_views_and_the_sql_check(client):
    res = client.post("/api/canvas/code", json={"steps": [PT, SLIDES]}).json()
    assert "WITH" in res["sql"] and "s2_join_slides" in res["sql"] and res["lines"]["2"]
    assert "def " in res["python"] and '"op": "join"' in res["json"]
    if sql.available():
        assert res["verified"]["ok"] is True and res["verified"]["cohort"]["match"]
    else:
        assert res["verified"]["ok"] is None


def test_patient_tree_and_gallery(client):
    p = client.get("/api/canvas/patients/TCGA-ZZ-0001").json()
    assert [s["sample_type"] for s in p["samples"]] == ["Primary Tumor", "Solid Tissue Normal"]
    assert len(p["samples"][0]["slides"]) == 3 and p["samples"][0]["layers"]["rna"]
    assert client.get("/api/canvas/patients/TCGA-ZZ-9999").status_code == 404
    g = client.post("/api/canvas/gallery", json={"steps": [PT, SLIDES], "limit": 2}).json()
    assert g["kinds"] == ["diagnostic (FFPE)"] and len(g["items"]) == 2 and g["total"] >= 2
    assert all(it["slide_kind"] == "diagnostic (FFPE)" for it in g["items"])


def test_thumbnails_refuse_bad_names_and_missing_images(client):
    assert client.get("/api/canvas/thumbs/not-a-barcode.jpg").status_code == 400
    # the fixture's slides have no IDC series, so no image is requested from the network
    assert client.get("/api/canvas/thumbs/TCGA-ZZ-0001-01Z-00-DX1.jpg").status_code == 404


def test_thumbnail_crop_falls_back_without_pillow(monkeypatch):
    # the hosted backend installs no Pillow: slide images must still be served, just uncropped
    from frontend.backend import canvas
    monkeypatch.setitem(sys.modules, "PIL", None)
    assert canvas._crop_to_tissue(b"\xff\xd8raw-jpeg") == b"\xff\xd8raw-jpeg"


def test_gene_lookup_for_the_block_library(client):
    hit = client.get("/api/canvas/genes", params={"q": "tp53"}).json()
    assert hit["exact"] and hit["matches"][0] == {"value": "TP53", "count": 3}
    assert [m["value"] for m in client.get("/api/canvas/genes", params={"q": "PIK"}).json()["matches"]] == ["PIK3CA"]
    typo = client.get("/api/canvas/genes", params={"q": "TP35"}).json()
    assert not typo["exact"] and typo["matches"] == [] and typo["closest"][0] == "TP53"
    assert client.get("/api/canvas/genes", params={"q": "ZZQQXX"}).json() == {"query": "ZZQQXX", "exact": False, "matches": [], "closest": []}
    assert client.get("/api/canvas/genes", params={"q": "<script>"}).json()["query"] == "SCRIPT"
