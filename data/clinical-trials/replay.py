"""Replay the organizer-authored, fictional date rule; not a screening tool."""
import csv
from datetime import date
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
rules = json.loads((HERE / "fictional_rules.json").read_text())["versions"]

def verdict(reference, treatment, minimum):
    try:
        days = (date.fromisoformat(reference) - date.fromisoformat(treatment)).days
    except ValueError:
        return "needs_review"
    if days < 0:
        return "needs_review"
    return "meets_rule" if days >= minimum else "does_not_meet_rule"

if __name__ == "__main__":
    with (HERE / "fictional_cases.csv").open() as f:
        print("case_id", *(r["id"] for r in rules), sep=",")
        for case in csv.DictReader(f):
            print(case["case_id"], *(verdict(case["reference_date"],
                  case["last_fictional_treatment_date"], r["min_days"]) for r in rules), sep=",")
