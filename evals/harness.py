"""Shared pieces of the parser benchmark: load cases, turn a spec into the exported sample set, score it."""
import csv
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cohort_studio import engine  # noqa: E402

EVALS = pathlib.Path(__file__).resolve().parent
CASES_FILE = EVALS / "cases.json"
EXPECTED_DIR = EVALS / "expected"


def load_cases(split="all", ids=None):
    cases = json.loads(CASES_FILE.read_text())["cases"]
    by_id = {c["id"]: c for c in cases}
    for c in cases:
        if "gold_from" in c:   # a paraphrase shares another case's answer
            c["gold_steps"] = by_id[c["gold_from"]]["gold_steps"]
    if ids:
        wanted = set(ids)
        cases = [c for c in cases if c["id"] in wanted]
    elif split != "all":
        cases = [c for c in cases if c.get("split", "dev") == split]
    return cases


def gold_spec(case):
    return {"request": case["request"], "steps": case["gold_steps"], "notes": [], "unparsed": [],
            "unparsed_confirmed": True, "parser": "gold"}


def build_cohort(spec, store):
    """Exactly what `python -m cohort_studio` writes to cohort_samples.csv: run, fix inflating joins, run again.

    Returns (sample_ids, error). An empty list with an error means the spec could not run.
    """
    result = engine.run(spec, store)
    if any(s.inflated for s in result.steps):
        spec, _ = engine.apply_fixes(spec, result)
        result = engine.run(spec, store)
    errors = [m for _, m in result.problems] + [s.error for s in result.steps if s.error]
    if errors:
        return [], "; ".join(dict.fromkeys(errors))
    return list(result.cohort_ids), ""


def read_expected(case_id):
    path = EXPECTED_DIR / f"{case_id}.csv"
    if not path.exists():
        return None
    with open(path) as f:
        return [row["sample_id"] for row in csv.DictReader(f)]


def write_expected(case_id, ids):
    EXPECTED_DIR.mkdir(exist_ok=True)
    with open(EXPECTED_DIR / f"{case_id}.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sample_id"])
        for sid in sorted(ids):
            w.writerow([sid])


def score(predicted, expected):
    """Set comparison, like the organizers' checker: exact match plus how far off a miss is."""
    p, e = set(predicted), set(expected)
    union = p | e
    return {"exact": p == e, "n_pred": len(p), "n_gold": len(e), "missing": len(e - p), "extra": len(p - e),
            "jaccard": (len(p & e) / len(union)) if union else 1.0,
            "examples_missing": sorted(e - p)[:3], "examples_extra": sorted(p - e)[:3]}
