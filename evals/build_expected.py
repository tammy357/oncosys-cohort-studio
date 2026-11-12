"""Freeze the answer key: run every case's gold steps and write evals/expected/<id>.csv.

    python evals/build_expected.py           # (re)write every expected CSV and print the counts
    python evals/build_expected.py --check   # fail if the engine no longer reproduces the frozen CSVs

Needs the raw files: python data/tcga-brca/fetch.py --no-rna
"""
import argparse
import sys

from harness import build_cohort, gold_spec, load_cases, read_expected, write_expected

from cohort_studio.data import DataStore
from cohort_studio.spec import validate


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="compare with the frozen CSVs instead of writing them")
    ap.add_argument("--data-dir", help="folder with sample_manifest.csv (default: data/tcga-brca)")
    args = ap.parse_args(argv)

    store = DataStore(args.data_dir)
    if not store.available("clinical"):
        print("Clinical/survival/mutation files are missing: run python data/tcga-brca/fetch.py --no-rna")
        return 2
    bad = 0
    print(f"{'case':<28} {'split':<8} {'samples':>7} {'patients':>8}  status")
    for case in load_cases():
        spec = gold_spec(case)
        problems = validate(spec, store)
        ids, error = build_cohort(spec, store)
        patients = len({s[:12] for s in ids})
        if problems or error:
            bad += 1
            status = f"GOLD SPEC FAILS: {problems or error}"
        elif not ids:
            bad += 1
            status = "EMPTY cohort: a gold case should select at least one sample"
        elif args.check:
            frozen = read_expected(case["id"])
            if frozen is None:
                bad += 1
                status = "no frozen CSV"
            elif set(frozen) != set(ids):
                bad += 1
                status = f"CHANGED: frozen {len(frozen)}, engine now {len(ids)}"
            else:
                status = "matches frozen"
        else:
            write_expected(case["id"], ids)
            status = "written"
        print(f"{case['id']:<28} {case.get('split', 'dev'):<8} {len(ids):>7} {patients:>8}  {status}")
    print(f"\n{bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
