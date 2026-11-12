"""Check a findings file against the shared format, and that every finding id a repair or decision names exists.

Usage: python format/validate.py <findings.json> [more.json ...]
Needs: pip install jsonschema
"""
import json, pathlib, sys

try:
    import jsonschema
except ImportError:
    sys.exit("Install the validator first: pip install jsonschema")

SCHEMA = json.loads((pathlib.Path(__file__).resolve().parent / "findings.schema.json").read_text())

def check(path):
    doc = json.loads(pathlib.Path(path).read_text())
    errors = [f"{'/'.join(map(str, e.path)) or '(top)'}: {e.message}"
              for e in jsonschema.Draft202012Validator(SCHEMA).iter_errors(doc)]
    finding_ids = {f.get("id") for f in doc.get("findings", [])}
    repair_ids = {r.get("id") for r in doc.get("repairs", [])}
    for r in doc.get("repairs", []):
        errors += [f"repairs/{r.get('id')}: addresses unknown finding '{a}'" for a in r.get("addresses", []) if a not in finding_ids]
    for d in doc.get("decisions", []):
        if d.get("repair_id") not in repair_ids:
            errors.append(f"decisions: unknown repair '{d.get('repair_id')}'")
    return errors

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    bad = 0
    for p in sys.argv[1:]:
        errs = check(p)
        bad += bool(errs)
        print(f"{'valid  ' if not errs else 'INVALID'} {p}")
        for e in errs:
            print("   -", e)
    sys.exit(1 if bad else 0)
