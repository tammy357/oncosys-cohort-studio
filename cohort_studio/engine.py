"""Run a cohort spec step by step and record what every step did.

For each step the ledger records rows, distinct samples and distinct patients, which samples
it removed and why, whether the number of rows grew faster than the number of samples
(join inflation), and how the make-up of the cohort shifted.
"""
from dataclasses import dataclass, field

import pandas as pd

from . import ops
from .data import EXPECTED_LINKS, FIELD_TO_COMPOSITION
from .spec import (JOIN_TABLES, LAYER_LABELS, clone, condition_sources, condition_tests, describe_step,
                   resolve_condition, validate)
from .stats import permutation_shift_tests

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
    missing_fraction: float = 0.0   # filter / where steps: share of incoming samples with no recorded value to decide on
    column: str = ""                # filter steps: the real column used
    selection: list = field(default_factory=list)       # one_per_patient: which sample was kept, and why
    removed_reasons: dict = field(default_factory=dict)  # per-sample reasons, where a step has them

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
    cols = [c for c in comp.columns if c != "patient_id" and c not in skip]
    tests = permutation_shift_tests(b, after_ids, cols)
    for col in cols:
        d, biggest = tvd(b[col].dropna(), a[col].dropna())
        if d is not None:
            out[col] = {"tvd": round(d, 3), **biggest}
            if col in tests:
                out[col].update({"p_value": tests[col]["p_value"],
                                 "random_tvd_median": tests[col]["random_tvd_median"],
                                 "random_tvd_95": tests[col]["random_tvd_95"]})
    return out

def _right_table(store, step):
    table = step["table"]
    if table == "slides":
        return ops.slides_of_kind(store.slides, step.get("slide_kinds") or [])
    if table == "normals":
        if step.get("require_layers"):
            return ops.normal_samples_with_layers(store.manifest, step["require_layers"])
        return ops.normal_samples(store.manifest)
    return store.table(table)


def _skip_columns_for(step):
    if step["op"] == "sample_type":
        return {"Sample type"}
    if step["op"] == "filter":
        col = FIELD_TO_COMPOSITION.get((step["table"], step["field"]))
        return {col} if col else set()
    if step["op"] == "any_of":
        cols = {FIELD_TO_COMPOSITION.get((step["table"], c.get("field"))) for c in step.get("conditions", [])}
        return {c for c in cols if c}
    if step["op"] == "where":        # what the condition tests changes on purpose, like a filter
        tests = condition_tests(step.get("condition") or {})
        cols = {FIELD_TO_COMPOSITION.get((t.get("table"), t.get("field"))) for t in tests if "table" in t}
        cols |= {"Sample type"} if any("sample_type" in t for t in tests) else set()
        return {c for c in cols if c}
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
    if op == "exclude_layer":
        return f"has {LAYER_LABELS.get(step['layer'], step['layer'])} data"
    if op == "any_of":
        return "met none of the conditions (or the values are not recorded)"
    if op == "one_per_patient":
        return "another sample from the same patient was kept"
    if op == "where":
        return "didn't meet the condition (or a value it needs isn't recorded)"
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
    if op == "one_per_patient":
        rule = step.get("rule", "priority")
        time_col = None
        if rule in ("earliest", "latest"):
            time_col = f"{PREFIX['clinical']}.{store.resolve_field('clinical', 'collection_day')}"
        return ops.one_sample_per_patient(frame, rule, step.get("order"), time_col)
    if op == "exclude_layer":
        return ops.exclude_layer(frame, step["layer"])
    if op == "any_of":
        p = PREFIX[step["table"]]
        conditions = [(f"{p}.{store.resolve_field(step['table'], c['field'])}", c["cmp"], c.get("value"))
                      for c in step["conditions"]]
        return ops.filter_any(frame, conditions)
    if op == "where":
        return ops.filter_where(frame, resolve_condition(step["condition"], store),
                                condition_tables(step["condition"], store), step.get("apply_to", "sample"))
    raise ValueError(f"Unknown step '{op}'")


def _where_unknown(step, store, before):
    """For the missing-data check: the share of incoming samples the condition couldn't decide (a value it needs isn't
    recorded), and the fields it tests."""
    cols = {"sample": ["sample_id"], "added_normals": ["normal.sample_id"],
            "both": ["sample_id", "normal.sample_id"]}.get(step.get("apply_to", "sample"), ["sample_id"])
    ids = [i for c in cols if c in before.columns for i in before[c].drop_duplicates()]
    if not ids:
        return 0.0, ""
    truth = ops.evaluate_condition(resolve_condition(step["condition"], store), ids, condition_tables(step["condition"], store))
    tests = condition_tests(step["condition"])
    names = [t["field"] for t in tests if "table" in t] + [f"{t['gene']} mutation call" for t in tests if "gene" in t]
    return float(truth.isna().mean()), ", ".join(dict.fromkeys(names)) or "the condition"


def _expected_links(step):
    """Characteristics a step is expected to move because of what it filters on (filters, any_of and conditions)."""
    if step["op"] == "filter":
        tested = [(step.get("table"), step.get("field"))]
    elif step["op"] == "any_of":
        tested = [(step.get("table"), c.get("field")) for c in step.get("conditions", [])]
    elif step["op"] == "where":
        tested = [(t.get("table"), t.get("field")) for t in condition_tests(step.get("condition") or {}) if "table" in t]
    else:
        return {}
    links = {}
    for key in tested:
        links.update(EXPECTED_LINKS.get(key, {}))
    return links


def condition_tables(condition, store):
    """The loaded tables a 'where' condition reads (see ops.evaluate_condition)."""
    loaders = {"manifest": lambda: store.manifest, "slides": lambda: store.slides,
               "mutation": lambda: store.table("mutation"), "clinical": lambda: store.table("clinical"),
               "survival": lambda: store.table("survival")}
    return {name: loaders[name]() for name in condition_sources(condition)}


def _record_where(r, step, store, before):
    """Why each sample left a 'where' step: the condition was false, or a value it needs isn't recorded, for the
    sample itself or (apply_to both / added_normals) for the normal sample its pair added."""
    if not r.removed:
        return
    cond, tables = resolve_condition(step["condition"], store), condition_tables(step["condition"], store)
    apply_to = step.get("apply_to", "sample")
    own = dict(zip(r.removed, ops.evaluate_condition(cond, r.removed, tables)))
    paired = {}
    if apply_to != "sample" and "normal.sample_id" in before.columns:
        pairs = before.loc[before["sample_id"].isin(r.removed), ["sample_id", "normal.sample_id"]].drop_duplicates()
        for sid, t in zip(pairs["sample_id"], ops.evaluate_condition(cond, pairs["normal.sample_id"], tables)):
            paired[sid] = min(paired.get(sid, 1.0), t) if t == t else float("nan")

    def why(t):
        return "doesn't meet the condition" if t == 0.0 else "a value the condition needs isn't recorded"
    for sid in r.removed:
        normal = paired.get(sid, 1.0)
        if apply_to != "sample" and normal != 1.0 and (apply_to == "added_normals" or own[sid] == 1.0):
            r.removed_reasons[sid] = ("its paired normal sample doesn't meet the condition" if normal == 0.0
                                      else "a value the condition needs isn't recorded for its paired normal sample")
        else:
            r.removed_reasons[sid] = why(own[sid])


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


def _record_selection(r, before, after):
    """For a one_per_patient step: which sample each patient kept, which were dropped, and why."""
    kept = after.drop_duplicates("sample_id").set_index("patient_id")
    sample_patient = before.drop_duplicates("sample_id").set_index("sample_id")["patient_id"]
    for sid in r.removed:
        pid = sample_patient[sid]
        r.removed_reasons[sid] = f"another sample from the same patient was kept ({kept.loc[pid, 'sample_id']})"
    dropped = {}
    for sid in r.removed:
        dropped.setdefault(sample_patient[sid], []).append(sid)
    for pid, sids in sorted(dropped.items()):
        r.selection.append({"patient_id": pid, "kept": kept.loc[pid, "sample_id"],
                            "kept_type": kept.loc[pid, "sample_type"], "dropped": sorted(sids),
                            "reason": kept.loc[pid, "selection.reason"]})


def _mark_expected(shift, links):
    for var, info in shift.items():
        info["expected"] = links.get(var, "")
    return shift


def extra_cohort_columns(spec):
    return [f"{PREFIX[s['table']]}.sample_id" for s in spec.get("steps", [])
            if s.get("op") == "join" and s.get("add_to_cohort") and s.get("mode", "rows") == "rows"]


def run(spec, store):
    problems = validate(spec, store)
    frame = store.manifest.copy()
    start_ids = list(frame["sample_id"].unique())
    filtered_vars = set()
    all_links = {}
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
        if step["op"] == "one_per_patient":
            _record_selection(r, frame, new)
        if step["op"] == "where":
            _record_where(r, step, store, frame)
            r.missing_fraction, r.column = _where_unknown(step, store, frame)
        ratio_in = r.rows_in / r.samples_in if r.samples_in else 1
        if r.samples_out and r.rows_per_sample > ratio_in + 1e-9:
            r.inflated = True
            extra = r.rows_out - r.samples_out
            r.inflation_note = (f"Rows grew to {r.rows_out:,} but there are only {r.samples_out:,} distinct samples "
                                f"({r.patients_out:,} patients): {extra:,} extra rows would count samples more than once.")
            r.duplicate_examples = _duplicate_examples(new, step)
        skip = _skip_columns_for(step)
        filtered_vars |= skip
        links = _expected_links(step)
        all_links.update(links)
        shift = composition_shift(store, before_ids, after_ids, skip)
        if step["op"] == "sample_type":
            # Choosing tumors over normals changes everything else on purpose; it isn't a loss of samples.
            links = {var: "this step chooses the sample types you asked for" for var in shift}
        r.shift = _mark_expected(shift, links)
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
    overall = _mark_expected(composition_shift(store, baseline, frame["sample_id"].unique(), filtered_vars),
                             all_links) if not failed else {}
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
