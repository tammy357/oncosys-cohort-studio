"""Command line: build a TCGA-BRCA cohort from a plain-English request or a cohort definition.

    python -m cohort_studio "primary tumors with RNA and protein data and a diagnostic slide"
    python -m cohort_studio --spec output/cohort_spec.json       # a definition (JSON steps), run exactly as written
    python -m cohort_studio --file my_cohorts.json               # one or many requests/definitions from a file

Requests are drafted by AI when it's available: your own key or local model (environment or .env), otherwise the
hosted Cohort Studio service, otherwise the built-in parser (--rules uses only the parser; --no-hosted never sends
the request to the hosted service). Prints every step (rows, samples, patients, inflation), fixes inflating joins
unless --no-fix, prints the verdict, and writes cohort_samples.csv plus a reproduce script and the other exports to
--out (one sub-folder per cohort for --file).
"""
import argparse
import csv
import json
import pathlib
import sys

from . import exports
from .verdict import PROBLEM_HEADLINES
from .api import as_query, build_cohort, folder_name, load_queries
from .data import DataStore


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m cohort_studio", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("request", nargs="?", help="the cohort you want, in plain English")
    ap.add_argument("--spec", help="run a cohort definition (e.g. a saved cohort_spec.json) exactly as written")
    ap.add_argument("--file", help="a .txt (one request per line) or .json file (one or many requests/definitions)")
    ap.add_argument("--data-dir", help="folder with sample_manifest.csv (default: data/tcga-brca)")
    ap.add_argument("--out", default="output", help="folder for cohort_samples.csv and the other exports")
    ap.add_argument("--no-fix", action="store_true", help="don't fix inflating joins (shows the problem only)")
    ap.add_argument("--accept-unparsed", action="store_true", help="confirm that unused words don't change the cohort and that open readings are what you meant")
    ap.add_argument("--rules", action="store_true", help="built-in parser only: no AI, nothing leaves this computer")
    ap.add_argument("--no-hosted", action="store_true", help="your own key or local model only, never the hosted service")
    ap.add_argument("--ai", choices=["anthropic", "openai"], help="which provider your own key is for (default: "
                                                                    "whichever key is set)")
    ap.add_argument("--team", default="Cohort Studio team", help="team name for findings.json")
    ap.add_argument("--fields", action="store_true", help="list the fields and values available in this release")
    args = ap.parse_args(argv)

    store = DataStore(args.data_dir)
    if args.fields:
        return list_fields(store)
    if args.spec:
        queries = [json.load(open(args.spec, encoding="utf-8-sig"))]
    elif args.file:
        queries = load_queries(args.file)
    elif args.request:
        queries = [args.request]
    else:
        ap.print_help()
        return 2
    options = {"ai": "off" if args.rules else "own" if args.no_hosted else "auto", "provider": args.ai,
               "fix": not args.no_fix, "accept_unparsed": args.accept_unparsed, "team": args.team}
    if len(queries) == 1:
        run = build_cohort(queries[0], out_dir=args.out, store=store, log=print, **options)
        report(run, store)
        return 0 if run.ok else 1
    rows = []
    for i, query in enumerate(queries, 1):
        try:
            as_query(query)
        except ValueError as exc:
            print(f"\n#{i}: skipped ({exc})")
            rows.append({"#": i, "request": str(query)[:80], "error": str(exc)})
            continue
        print(f"\n{'=' * 100}\n#{i} of {len(queries)}")
        run = build_cohort(query, out_dir=f"{args.out}/{folder_name(i, query)}", store=store, log=print, **options)
        report(run, store)
        rows.append({"#": i, "request": run.request, "samples": run.n_samples,
                     "patients": run.result.final_patients if run.ok else "", "verdict": run.verdict.verdict,
                     "drafted by": run.route["label"], "folder": str(run.out_dir), "error": "" if run.ok else "step failed"})
    pathlib.Path(args.out).mkdir(parents=True, exist_ok=True)
    with open(f"{args.out}/summary.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["#", "request", "samples", "patients", "verdict", "drafted by", "folder",
                                                "error"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n{'=' * 100}\n{len(rows)} cohorts; summary in {args.out}/summary.csv")
    for r in rows:
        print(f"  #{r['#']:<3} {str(r.get('samples', '')):>5} samples  {r.get('verdict', '') or r['error']:<22} {r['request'][:60]}")
    return 0 if all(not r["error"] for r in rows) else 1


def report(run, store):
    spec, result = run.spec, run.result
    print(f"\nRequest: {spec.get('request', '')}")
    print(f"Drafted by: {run.route['label']}")
    for note in spec.get("notes", []):
        print(f"  note: {note}")
    if spec.get("unparsed"):
        print(f"  not used: {', '.join(spec['unparsed'])}")
    for reading in spec.get("unconfirmed") or []:
        print(f"  not confirmed: {reading}")
    print_ledger(run.first_result)
    if run.fixes:
        print("\nFixed the join inflation:")
        for c in run.fixes:
            print(f"  - {c}")
        print_ledger(result)

    v = run.verdict
    print(f"\nVERDICT: {v.verdict} ({v.headline or v.reason})")
    for c in v.checks:
        name = c.name if c.status == "pass" else PROBLEM_HEADLINES.get(c.name, c.name)
        print(f"  [{c.status:^7}] {name}: {c.detail}")
    print("  Before using this cohort, a person should:")
    for r in v.human_review:
        print(f"   - {r}")

    sel = exports.selection_frame(result)
    if len(sel):
        print(f"\nOne sample per patient: chose for {len(sel)} patients with more than one sample, e.g.")
        for _, row in sel.head(3).iterrows():
            print(f"  {row['patient_id']}: kept {row['kept']}, dropped {row['dropped']} ({row['reason']})")
    cov = exports.coverage_frame(result, store)
    if len(cov):
        well = int((cov["recorded %"] >= 80).sum())
        print(f"\nRecorded for this cohort: {well} of {len(cov)} clinical and survival variables have a value for at "
              f"least 80% of its samples (full list in coverage.csv).")
    if not result.ok:
        print("\n!! A step failed, so cohort_samples.csv is empty. Fix the step (see above) and run again.")
    print(f"\nWrote {len(result.cohort_ids)} samples to {run.out_dir / 'cohort_samples.csv'} "
          f"(plus reproduce_cohort.py, cohort_spec.json, ledger.csv, exclusions.csv, sample_selection.csv, "
          f"coverage.csv, verdict.json, findings.json)")


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
