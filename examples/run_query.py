"""Build a cohort from a script: edit QUERY, then run  python examples/run_query.py  from the repository root.

QUERY is plain English, or a cohort definition (a dict with "steps", like an exported cohort_spec.json) that runs
exactly as written. The result is in OUT: cohort_samples.csv (one row per sample, a sample_id column with the
15-character TCGA barcode), reproduce_cohort.py, the ledger, the verdict and the other exports.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from cohort_studio.api import build_cohort  # noqa: E402

QUERY = "primary tumors with both RNA and protein data and a diagnostic slide"
OUT = "output/my_cohort"

run = build_cohort(QUERY, out_dir=OUT, log=print)      # ai="off" for the built-in parser only
print(f"\nDrafted by: {run.route['label']}")
for step in run.result.steps:
    print(f"  {step.index + 1}. {step.label}: {step.samples_out} samples, {step.patients_out} patients")
if run.fixes:
    print("Inflation fixed:", "; ".join(run.fixes))
print(f"Verdict: {run.verdict.verdict}. {run.verdict.reason}")
print(f"{run.n_samples} samples -> {run.out_dir / 'cohort_samples.csv'}")
