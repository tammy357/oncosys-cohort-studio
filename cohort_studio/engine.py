"""Run a cohort spec step by step and record what every step did.

For each step the ledger records rows, distinct samples and distinct patients, which samples
it removed and why, whether the number of rows grew faster than the number of samples
(join inflation), and how the make-up of the cohort shifted.
"""
from dataclasses import dataclass, field

import pandas as pd

from . import ops
from .data import FIELD_TO_COMPOSITION
from .spec import JOIN_TABLES, LAYER_LABELS, clone, describe_step, validate

PREFIX = {"slides": "slide", "clinical": "clin", "survival": "surv", "normals": "normal"}


@dataclass
class StepResult:
    index: int
    label: str
    op: str
    rows_in: int = 0
    rows_out: int = 0
    samples_in: int = 0
    samples_out: int = 0
    patients_in: int = 0
    patients_out: int = 0
    inflated: bool = False
    inflation_note: str = ""
    duplicate_examples: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    removed_reason: str = ""
    shift: dict = field(default_factory=dict)
    error: str = ""
    skipped: bool = False
    missing_fraction: float = 0.0   # filter steps: share of incoming samples with no recorded value
    column: str = ""                # filter steps: the real column used

    @property
    def rows_per_sample(self):
        return self.rows_out / self.samples_out if self.samples_out else 0.0


@dataclass
class RunResult:
    spec: dict
    steps: list
    frame: pd.DataFrame
    cohort_ids: list
    problems: list
    start_samples: list
    overall_shift: dict
    final_rows: int
    final_samples: int
    final_patients: int

    @property
    def unresolved_inflation(self):
        return self.final_rows > self.final_samples

    @property
    def ok(self):
        return not self.problems and not any(s.error for s in self.steps)


def tvd(before, after):
    """Total variation distance between two category distributions (0 = same mix, 1 = no overlap)."""
    if len(before) == 0 or len(after) == 0:
        return None, None
    p = before.value_counts(normalize=True)
    q = after.value_counts(normalize=True)
    idx = p.index.union(q.index)
    diff = q.reindex(idx, fill_value=0) - p.reindex(idx, fill_value=0)
    biggest = diff.abs().idxmax()
    return float(0.5 * diff.abs().sum()), {"group": str(biggest),
                                            "before_pct": round(100 * float(p.get(biggest, 0)), 1),
                                            "after_pct": round(100 * float(q.get(biggest, 0)), 1)}


def composition_shift(store, before_ids, after_ids, skip=()):
    comp = store.composition_frame()
    out = {}
    if not len(before_ids) or not len(after_ids):
        return out
    b = comp.reindex(list(before_ids))
    a = comp.reindex(list(after_ids))
    for col in comp.columns:
        if col == "patient_id" or col in skip:
            continue
        d, biggest = tvd(b[col].dropna(), a[col].dropna())
        if d is not None:
            out[col] = {"tvd": round(d, 3), **biggest}
    return out


def _right_table(store, step):
    table = step["table"]
    if table == "slides":
        return ops.slides_of_kind(store.slides, step.get("slide_kinds") or [])
    if table == "normals":
        return ops.normal_samples(store.manifest)
    return store.table(table)


def _skip_columns_for(step):
    if step["op"] == "sample_type":
        return {"Sample type"}
    if step["op"] == "filter":
        col = FIELD_TO_COMPOSITION.get((step["table"], step["field"]))
        return {col} if col else set()
    return set()


def _removed_reason(step):
    op = step["op"]
    if op == "sample_type":
        return "a different sample type"
    if op == "layer":
        return f"no {LAYER_LABELS.get(step['layer'], step['layer'])} data"
    if op == "join":
        return f"no matching {JOIN_TABLES[step['table']]['label']}"
    if op == "filter":
        return "did not meet the filter (or the value is not recorded)"
    if op == "mutation":
        return "mutation call didn't match, or the sample has no mutation data"
    return "removed by this step"


def apply_step(frame, step, store):
    op = step["op"]
    if op == "sample_type":
        return ops.keep_sample_types(frame, step["values"])
    if op == "layer":
        return ops.require_layer(frame, step["layer"])
    if op == "join":
        right = _right_table(store, step)
        return ops.join_table(frame, right, PREFIX[step["table"]], step["key"], step.get("mode", "rows"))
    if op == "filter":
        col = store.resolve_field(step["table"], step["field"])
        return ops.filter_column(frame, f"{PREFIX[step['table']]}.{col}", step["cmp"], step.get("value"))
    if op == "mutation":
        return ops.filter_mutation(frame, store.table("mutation"), step["gene"], step.get("mutated", True))
    if op == "dedupe":
        return ops.one_row_per_sample(frame)
    raise ValueError(f"Unknown step '{op}'")


def _duplicate_examples(frame, step, limit=5):
    counts = frame["sample_id"].value_counts()
    dup = counts[counts > 1].head(limit)
    examples = []
    for sid, n in dup.items():
        ex = {"sample_id": sid, "rows": int(n)}
        if step["op"] == "join":
            p = PREFIX[step["table"]]
            id_col = f"{p}.slide_barcode" if step["table"] == "slides" else f"{p}.sample_id"
            if id_col in frame.columns:
                ex["matched"] = sorted(frame.loc[frame["sample_id"] == sid, id_col].astype(str).unique().tolist())[:6]
        examples.append(ex)
    return examples


def extra_cohort_columns(spec):
    return [f"{PREFIX[s['table']]}.sample_id" for s in spec.get("steps", [])
            if s.get("op") == "join" and s.get("add_to_cohort") and s.get("mode", "rows") == "rows"]


def run(spec, store):
    problems = validate(spec, store)
    frame = store.manifest.copy()
    start_ids = list(frame["sample_id"].unique())
    filtered_vars = set()
    results = []
    failed = False
    for i, step in enumerate(spec.get("steps", [])):
        r = StepResult(index=i, label=describe_step(step, store), op=step.get("op", "?"))
        r.rows_in, r.samples_in, r.patients_in = len(frame), frame["sample_id"].nunique(), frame["patient_id"].nunique()
        step_problems = [m for (j, m) in problems if j == i]
        if failed or step_problems:
            r.skipped = True
            r.error = "; ".join(step_problems) if step_problems else "Not run because an earlier step failed."
            failed = True
            results.append(r)
            continue
        try:
            new = apply_step(frame, step, store)
        except Exception as exc:  # show the error in the ledger instead of crashing the app
            r.error, r.skipped, failed = str(exc), True, True
            results.append(r)
            continue
        r.rows_out, r.samples_out, r.patients_out = len(new), new["sample_id"].nunique(), new["patient_id"].nunique()
        if step["op"] == "filter":
            col = store.resolve_field(step["table"], step["field"])
            r.column = col
            per_sample = frame.drop_duplicates("sample_id")[f"{PREFIX[step['table']]}.{col}"]
            r.missing_fraction = float(per_sample.map(ops.is_missing).mean()) if len(per_sample) else 0.0
        before_ids = set(frame["sample_id"])
        after_ids = set(new["sample_id"])
        r.removed = sorted(before_ids - after_ids)
        r.removed_reason = _removed_reason(step)
        ratio_in = r.rows_in / r.samples_in if r.samples_in else 1
        if r.samples_out and r.rows_per_sample > ratio_in + 1e-9:
            r.inflated = True
            extra = r.rows_out - r.samples_out
            r.inflation_note = (f"Rows grew to {r.rows_out:,} but there are only {r.samples_out:,} distinct samples "
                                f"({r.patients_out:,} patients): {extra:,} extra rows would count samples more than once.")
            r.duplicate_examples = _duplicate_examples(new, step)
        skip = _skip_columns_for(step)
        filtered_vars |= skip
        r.shift = composition_shift(store, before_ids, after_ids, skip)
        results.append(r)
        frame = new
    extra = extra_cohort_columns(spec)
    cohort = [] if failed else ops.cohort_sample_ids(frame, extra)
    # The overall shift compares the final cohort with the population the request starts from
    # (after the sample-type step), so choosing tumors over normals isn't reported as a bias.
    baseline = start_ids
    steps = spec.get("steps", [])
    if steps and steps[0].get("op") == "sample_type" and results and not results[0].skipped:
        baseline = list(store.manifest[store.manifest["sample_type"].isin(steps[0]["values"])]["sample_id"])
    overall = composition_shift(store, baseline, frame["sample_id"].unique(), filtered_vars) if not failed else {}
    return RunResult(spec=spec, steps=results, frame=frame, cohort_ids=cohort, problems=problems,
                     start_samples=start_ids, overall_shift=overall,
                     final_rows=len(frame), final_samples=frame["sample_id"].nunique(),
                     final_patients=frame["patient_id"].nunique())


def suggest_fix(step):
    """A minimal change that stops a step counting samples twice, with a plain-words reason."""
    fixed = dict(step)
    if step["op"] == "join" and step["table"] in ("slides", "normals"):
        fixed["mode"] = "exists"
        what = "slide" if step["table"] == "slides" else "normal-tissue sample"
        return fixed, f"Keep each sample once if it has at least one matching {what}, instead of one row per {what}."
    if step["op"] == "join" and step.get("key") == "patient_id":
        fixed["key"] = "sample_id"
        return fixed, ("Match each sample to its own record by sample barcode. Joining on the patient barcode "
                       "also matches the patient's other samples (for example a normal-tissue sample).")
    return None, "Add a 'keep one row per sample' step after this one."


def apply_fixes(spec, result, only=None):
    """Return a copy of the spec with every inflating step fixed (or just step `only`), plus what changed."""
    new = clone(spec)
    changes = []
    inserts = []
    for r in result.steps:
        if not r.inflated or (only is not None and r.index != only):
            continue
        step = new["steps"][r.index]
        fixed, why = suggest_fix(step)
        if fixed is not None:
            new["steps"][r.index] = fixed
        else:
            inserts.append(r.index + 1)
        changes.append(f"Step {r.index + 1}: {why}")
        new.setdefault("fix_log", []).append({
            "step": r.index + 1, "was": r.label, "rows": r.rows_out, "samples": r.samples_out,
            "patients": r.patients_out, "examples": r.duplicate_examples[:5], "fix": why})
    for pos in sorted(inserts, reverse=True):
        new["steps"].insert(pos, {"op": "dedupe"})
    return new, changes
