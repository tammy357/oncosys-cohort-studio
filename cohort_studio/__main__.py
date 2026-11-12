"""Command line: python -m cohort_studio "primary tumors with RNA and protein data and a diagnostic slide"

Prints every step (rows, samples, patients, inflation), fixes inflating joins unless --no-fix,
prints the verdict, and writes cohort_samples.csv plus the other exports to --out.
"""
import argparse
import json
import sys

from . import engine, exports, verdict
from .data import DataStore
from .parse import parse_request


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m cohort_studio", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("request", nargs="?", help="the cohort you want, in plain English")
    ap.add_argument("--spec", help="run a saved cohort_spec.json instead of a request")
    ap.add_argument("--data-dir", help="folder with sample_manifest.csv (default: data/tcga-brca)")
    ap.add_argument("--out", default="output", help="folder for cohort_samples.csv and the other exports")
    ap.add_argument("--no-fix", action="store_true", help="don't fix inflating joins (shows the problem only)")
    ap.add_argument("--accept-unparsed", action="store_true", help="confirm that unused words don't change the cohort")
    ap.add_argument("--ai", choices=["anthropic", "openai"], help="draft the steps with an AI model (your own key or a local model)")
    ap.add_argument("--team", default="Cohort Studio team", help="team name for findings.json")
    ap.add_argument("--fields", action="store_true", help="list the fields and values available in this release")
    args = ap.parse_args(argv)

    store = DataStore(args.data_dir)
    if args.fields:
        return list_fields(store)
    if args.spec:
        spec = json.load(open(args.spec))
    elif args.request:
        spec = None
        if args.ai:
            from .llm import AIUnavailable, ai_parse
            try:
                spec, problems = ai_parse(args.request, store, provider=args.ai)
                if problems:
                    print("AI draft has problems, using the built-in parser instead:", problems)
                    spec = None
            except AIUnavailable as exc:
                print(exc)
        spec = spec or parse_request(args.request, store)
    else:
        ap.print_help()
        return 2
    if args.accept_unparsed:
        spec["unparsed_confirmed"] = True

    print(f"\nRequest: {spec.get('request', '')}")
    for note in spec.get("notes", []):
        print(f"  note: {note}")
    if spec.get("unparsed"):
        print(f"  not used: {', '.join(spec['unparsed'])}")

    result = engine.run(spec, store)
    print_ledger(result)
    if any(s.inflated for s in result.steps) and not args.no_fix:
        spec, changes = engine.apply_fixes(spec, result)
        print("\nFixed the join inflation:")
        for c in changes:
            print(f"  - {c}")
        result = engine.run(spec, store)
        print_ledger(result)

    v = verdict.evaluate(result, store)
    print(f"\nVERDICT: {v.verdict}\n  {v.reason}")
    for c in v.checks:
        print(f"  [{c.status:^7}] {c.name}: {c.detail}")
    print("  Before using this cohort, a person should:")
    for r in v.human_review:
        print(f"   - {r}")

    out = exports.write_all(result, v, store, args.out, team=args.team)
    print(f"\nWrote {len(result.cohort_ids)} samples to {out / 'cohort_samples.csv'} "
          f"(plus reproduce_cohort.py, cohort_spec.json, ledger.csv, exclusions.csv, verdict.json, findings.json)")
    return 0


def print_ledger(result):
    print(f"\n{'step':>4}  {'rows':>6} {'samples':>7} {'patients':>8}  what it does")
    print(f"{'':>4}  {len(result.start_samples):>6} {len(result.start_samples):>7} "
          f"{'':>8}  all samples in the release")
    for s in result.steps:
        if s.skipped:
            print(f"{s.index + 1:>4}  {'-':>6} {'-':>7} {'-':>8}  {s.label}  !! {s.error}")
            continue
        flag = "  << INFLATED: " + s.inflation_note if s.inflated else ""
        print(f"{s.index + 1:>4}  {s.rows_out:>6} {s.samples_out:>7} {s.patients_out:>8}  {s.label}{flag}")
        for ex in s.duplicate_examples[:3]:
            matched = f" ({', '.join(ex['matched'])})" if ex.get("matched") else ""
            print(f"{'':>30}e.g. {ex['sample_id']} appears {ex['rows']} times{matched}")


def list_fields(store):
    print("Files:")
    for name, st in store.file_status.items():
        print(f"  {name:<30} {'present' if st['present'] else 'missing':<8} {st['note']}")
    from .data import TABLE_FIELDS
    for table, fields in TABLE_FIELDS.items():
        print(f"\n{table}:")
        for f, info in fields.items():
            col = store.resolve_field(table, f)
            values = store.field_values(table, f, 8) if col else {}
            print(f"  {f:<12} -> {col or '(not in this release)'}  {values if values else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
