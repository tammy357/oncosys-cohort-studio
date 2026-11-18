"""What the cohort is for (its analytical intent), and the analysis that comes with it.

The intent is stored in the spec as
    "intended_use": {"outcome": "pfi", "compare_by": "pam50", "analysis": "survival"}
Every key is optional. The verdict uses it to check that the outcome is recorded (verdict.outcome_check) and that
every group being compared is large enough (groups_check here). The exports attach it to the cohort as
analysis_plan.json and analysis_starter.py, so the cohort and its first analysis travel together.

None of this fits a model or tests a hypothesis: the starter script only counts and summarises, so a person chooses
the real analysis.
"""
import json

import pandas as pd

from . import ops

SURVIVAL_EVENTS = {"os": "os_time", "pfi": "pfi_time"}
ANALYSES = ("survival", "compare_groups", "describe")
MIN_GROUP = 10   # fewer samples (or, for survival, events) than this in a compared group: Conditional


def analysis_of(use):
    """The analysis type: as given, else survival for a survival event, else compare_groups if there are groups."""
    if use.get("analysis"):
        return use["analysis"]
    if use.get("outcome") in SURVIVAL_EVENTS:
        return "survival"
    return "compare_groups" if use.get("compare_by") else "describe"


def table_for(store, field):
    """The table that holds a field: survival for survival fields, else clinical, else None."""
    for table in ("survival", "clinical"):
        if store.available(table) and store.resolve_field(table, field):
            return table
    return None


def field_series(store, field, ids):
    """(table, column, values indexed by sample_id) for a field over the given samples; missing values are None."""
    table = table_for(store, field)
    if table is None:
        return None, None, None
    col = store.resolve_field(table, field)
    t = store.table(table)
    t = t.drop_duplicates("sample_id").set_index("sample_id") if "sample_id" in t.columns else t
    values = t[col].reindex(list(ids))
    return table, col, values.map(lambda v: None if ops.is_missing(v) else v)


def groups_check(use, result, store, Check):
    """Each group named by compare_by has at least MIN_GROUP samples, or MIN_GROUP events for a survival outcome."""
    name = "Each group you compare is large enough"
    field = use["compare_by"]
    table, col, values = field_series(store, field, result.cohort_ids)
    if values is None:
        return Check(name, "unknown", f"This release has no field '{field}' to compare by (or its file isn't "
                                      "downloaded).", "Check the field's name: python -m cohort_studio --fields lists them.",
                     fields=[field])
    recorded = values.dropna().astype(str)
    counts = recorded.value_counts()
    missing = len(values) - len(recorded)
    events = None
    if use.get("outcome") in SURVIVAL_EVENTS:
        _, _, outcome = field_series(store, use["outcome"], result.cohort_ids)
        if outcome is not None:
            hit = pd.to_numeric(outcome, errors="coerce") == 1
            events = hit[recorded.index].groupby(recorded).sum().astype(int).reindex(counts.index, fill_value=0)
    parts = [f"{g}: {n:,} sample{'s' if n != 1 else ''}" + (f", {events[g]:,} event{'s' if events[g] != 1 else ''}" if events is not None else "")
             for g, n in counts.items()]
    detail = f"{store.field_label(table, field)} ({col}) splits the cohort into " + "; ".join(parts)
    if missing:
        detail += f"; {missing:,} not recorded"
    small = [g for g in counts.index
             if counts[g] < MIN_GROUP or (events is not None and events[g] < MIN_GROUP)]
    if len(counts) < 2:
        return Check(name, "warn", detail + ". There is only one group, so there is nothing to compare.",
                     "Choose a different field to compare by, or loosen the filters.", fields=[col])
    if small:
        what = "events" if events is not None else "samples"
        return Check(name, "warn", detail + f". Fewer than {MIN_GROUP} {what} in: " + ", ".join(small) + ".",
                     "Merge or drop the small groups before comparing, and say so in the methods.", fields=[col])
    return Check(name, "pass", detail + ".", fields=[col])


def analysis_plan(result, verdict, store):
    """The intent, with the real columns, the cohort files and the verdict, as one dict (analysis_plan.json)."""
    use = dict(result.spec.get("intended_use") or {})
    if not use:
        return None
    plan = {"request": result.spec.get("request", ""), "analysis": analysis_of(use), "cohort": "cohort_samples.csv",
            "n_samples": len(result.cohort_ids), "verdict": verdict.verdict}
    for key in ("outcome", "compare_by"):
        if use.get(key):
            table, col, _ = field_series(store, use[key], [])
            plan[key] = {"field": use[key], "table": table, "column": col}
    if use.get("outcome") in SURVIVAL_EVENTS:
        table, col, _ = field_series(store, SURVIVAL_EVENTS[use["outcome"]], [])
        plan["time"] = {"field": SURVIVAL_EVENTS[use["outcome"]], "table": table, "column": col}
    plan["checks"] = [{"check": c.name, "result": c.status, "detail": c.detail} for c in verdict.checks
                      if c.name in ("The outcome you named is recorded for the cohort",
                                    "Each group you compare is large enough",
                                    "Survival endpoint suits breast cancer")]
    from .data import DEFAULT_DATA_DIR
    import pathlib
    if pathlib.Path(store.data_dir).resolve() != pathlib.Path(DEFAULT_DATA_DIR).resolve():
        plan["data_dir"] = str(store.data_dir)
    plan["note"] = ("analysis_starter.py only counts and summarises (and, for survival, gives Kaplan-Meier medians). "
                    "It doesn't test anything: choose and pre-specify the real analysis yourself.")
    return plan


STARTER = '''"""First look at this cohort for its stated analysis, written by Cohort Studio with the cohort.

Request:  {request}
Analysis: {analysis}
Verdict:  {verdict}

It counts and summarises only; it fits no model and tests nothing. Run from the repository folder:
    python {out}/analysis_starter.py [DATA_DIR]
"""
import json
import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path.cwd()))
from cohort_studio.data import DataStore  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
PLAN = json.loads((HERE / "analysis_plan.json").read_text())
store = DataStore(sys.argv[1] if len(sys.argv) > 1 else PLAN.get("data_dir"))
ids = pd.read_csv(HERE / "cohort_samples.csv")["sample_id"].tolist()


def column(part):
    if part not in PLAN:
        return None
    table = store.table(PLAN[part]["table"]).drop_duplicates("sample_id").set_index("sample_id")
    return table[PLAN[part]["column"]].reindex(ids)


data = pd.DataFrame(index=ids)
for part in ("outcome", "time", "compare_by"):
    values = column(part)
    if values is not None:
        data[part] = values
group = data["compare_by"].fillna("(not recorded)").astype(str) if "compare_by" in data else pd.Series("all", index=ids)


def km_median(time, event):
    """Kaplan-Meier median time (None if the curve never falls to 0.5)."""
    d = pd.DataFrame({{"t": pd.to_numeric(time, errors="coerce"), "e": pd.to_numeric(event, errors="coerce")}}).dropna()
    surv, at_risk = 1.0, len(d)
    for t, g in d.sort_values("t").groupby("t"):
        events = int((g["e"] == 1).sum())
        if at_risk:
            surv *= 1 - events / at_risk
        if surv <= 0.5:
            return float(t)
        at_risk -= len(g)
    return None


rows = []
for name, idx in group.groupby(group).groups.items():
    sub = data.loc[idx]
    row = {{"group": name, "samples": len(sub)}}
    if PLAN["analysis"] == "survival" and "time" in sub:
        row["events"] = int((pd.to_numeric(sub["outcome"], errors="coerce") == 1).sum())
        row["median time (days, KM)"] = km_median(sub["time"], sub["outcome"])
    elif "outcome" in sub:
        values = sub["outcome"].dropna()
        numeric = pd.to_numeric(values, errors="coerce")
        if len(values) and numeric.notna().all():
            row["outcome median"] = float(numeric.median())
        else:
            row.update({{f"outcome = {{k}}": int(n) for k, n in values.astype(str).value_counts().items()}})
        row["outcome not recorded"] = int(sub["outcome"].isna().sum())
    rows.append(row)

print(pd.DataFrame(rows).to_string(index=False))
'''


def starter_script(plan, out_dir):
    return STARTER.format(request=plan["request"].replace('"""', "'"), analysis=plan["analysis"],
                          verdict=plan["verdict"], out=out_dir)


def write(result, verdict, store, out):
    """Write analysis_plan.json and analysis_starter.py next to the cohort when an intent was given."""
    plan = analysis_plan(result, verdict, store)
    if plan is None:
        return []
    (out / "analysis_plan.json").write_text(json.dumps(plan, indent=2, default=str))
    (out / "analysis_starter.py").write_text(starter_script(plan, out.name))
    return ["analysis_plan.json", "analysis_starter.py"]
