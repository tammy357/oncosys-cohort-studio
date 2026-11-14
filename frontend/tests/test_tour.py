"""Tours, practice and docs: the content stays consistent with the docs, the media exist, and the pages serve them."""
import json
import os
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["COHORT_STUDIO_NO_DOTENV"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from frontend.backend.config import Settings  # noqa: E402
from frontend.server import create_app  # noqa: E402
from tests.make_fixture import build  # noqa: E402

STATIC = ROOT / "frontend" / "static"
TOUR = STATIC / "tour"
CFG = json.loads((TOUR / "tours.json").read_text(encoding="utf-8"))
STEPS = [s for t in CFG["tours"].values() for s in t["steps"]]
DOCS = (STATIC / "docs.md").read_text(encoding="utf-8")


def slug(text):
    return re.sub(r"\s+", "-", re.sub(r"[^a-z0-9\s-]", "", re.sub(r"[`*_]", "", text.lower())).strip())


@pytest.fixture()
def client(tmp_path):
    data = build(tmp_path / "release")
    settings = Settings(data_dir=str(data), database_url=f"sqlite:///{(tmp_path / 'app.db').as_posix()}", ai_enabled=False)
    return TestClient(create_app(settings))


def test_docs_are_served_for_people_and_agents(client):
    page = client.get("/docs")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"] and "docs.js" in page.text
    md = client.get("/docs.md")
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    assert "## For AI agents" in md.text and "### Deep links" in md.text
    llms = client.get("/llms.txt")
    assert llms.status_code == 200 and llms.headers["content-type"].startswith("text/plain")
    assert llms.text.startswith("# Cohort Studio") and "/docs.md" in llms.text
    assert client.get("/static/tour/tours.json").json()["tours"].keys() == CFG["tours"].keys()


def test_every_tour_docs_link_points_at_a_heading():
    headings = {slug(m) for m in re.findall(r"^#{2,3} (.+)$", DOCS, re.M)}
    for st in STEPS:
        assert st["docs"] in headings, f"{st['id']} links to #{st['docs']}, which isn't a heading in docs.md"


def test_tour_text_stays_short():
    # no info bombing: a title is a few words, a body one or two short sentences
    for st in STEPS:
        assert len(st["title"].split()) <= 6, st["id"]
        assert len(st["body"].split()) <= 22, st["id"]
        assert len(st["narration"].split()) <= 40, st["id"]
    for tour in CFG["tours"].values():
        assert 3 <= len(tour["steps"]) <= 5


def test_every_step_has_its_voice_footage_and_captions():
    media = json.loads((TOUR / "media.json").read_text(encoding="utf-8"))
    for st in STEPS:
        for name in (f"{st['id']}.mp3", f"{st['id']}.mp4", f"{st['id']}.jpg", f"{st['id']}-dark.mp4", f"{st['id']}-dark.jpg"):
            assert (TOUR / name).stat().st_size > 1000, f"{name} missing (footage comes in light and dark)"
        m = media[st["id"]]
        assert m["captions"] and m["captions"][0]["s"] < 1.0, st["id"]
        assert m["video"] and m["video"] >= m["voice"], f"{st['id']}: the footage is shorter than its narration"
        assert m["dark"], f"{st['id']}: no dark-theme footage"
    previews = {t["preview"] for t in CFG["tours"].values()}
    assert previews <= {s["id"] for s in STEPS}


def test_practice_tasks_are_reported_by_the_pages():
    # each task id must be sent by some page with did('<id>'), and show a step that exists
    sources = "".join((STATIC / f).read_text(encoding="utf-8") for f in ("app.js", "canvas.js", "compare.js"))
    ids = {s["id"] for s in STEPS}
    for task in CFG["practice"]["tasks"]:
        assert f"did('{task['id']}')" in sources, f"no page reports practice task {task['id']}"
        assert task["show"] in ids and task["page"] in ("build", "canvas", "compare")
        assert task["target"] and task["coach"], f"{task['id']}: the coach needs a target and a line of guidance"
