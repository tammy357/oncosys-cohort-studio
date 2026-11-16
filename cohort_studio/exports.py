"""Write the outputs: cohort_samples.csv, a reproduce script, the spec, the ledger, the verdict and findings.json."""
import datetime
import inspect
import json
import pathlib

import pandas as pd

from . import ops
from .engine import PREFIX, extra_cohort_columns
from .spec import condition_sources, describe_step, resolve_condition
from .verdict import PROBLEM_HEADLINES, is_flagged_shift


def fixes_by_step(result):
    """The fix_log entries keyed by the step number they refer to in the current (fixed) spec. A fix that inserted a
    'keep one row per sample' step shifts every later step down by one."""
    out, inserted = {}, 0
    for fix in sorted(result.spec.get("fix_log", []), key=lambda f: f["step"]):
        out[fix["step"] + inserted] = fix
        inserted += 1 if fix.get("inserted_step") else 0
    return out


def ledger_frame(result):
    fixes = fixes_by_step(result)
    rows = []
    for s in result.steps:
        top = max(s.shift.items(), key=lambda kv: kv[1]["tvd"], default=(None, None))
        fix = fixes.get(s.index + 1)
        rows.append({
            "step": s.index + 1, "what it does": s.label,
            "rows": s.rows_out if not s.skipped else None,
            "samples": s.samples_out if not s.skipped else None,
            "patients": s.patients_out if not s.skipped else None,
            "samples removed": len(s.removed),
            "patients removed": max(s.patients_in - s.patients_out, 0) if not s.skipped else None,
            "rows before fix": fix["rows"] if fix else None,
            "inflated": "YES" if s.inflated else ("fixed" if fix else ""),
            "largest shift": (f"{top[0]} {top[1]['tvd']:.2f}" + (" (expected)" if top[1].get("expected") else ""))
                             if top[0] else "",
            "error": s.error,
        })
    frame = pd.DataFrame(rows)
    if len(frame):
        frame["rows before fix"] = frame["rows before fix"].astype("Int64")
    return frame


EXCLUSION_COLUMNS = ["sample_id", "removed_at_step", "step", "reason", "patient_id", "patient still in cohort"]


def _patients(result):
    if result.start_frame is None:
        return {}
    return result.start_frame.drop_duplicates("sample_id").set_index("sample_id")["patient_id"].to_dict()


def exclusions_frame(result):
    patient = _patients(result)
    kept = set(result.frame["patient_id"]) if result.cohort_ids else set()
    rows = []
    for s in result.steps:
        for sid in s.removed:
            pid = patient.get(sid, sid[:12])
            rows.append({"sample_id": sid, "removed_at_step": s.index + 1, "step": s.label,
                         "reason": s.removed_reasons.get(sid, s.removed_reason), "patient_id": pid,
                         "patient still in cohort": "yes" if pid in kept else "no"})
    return pd.DataFrame(rows, columns=EXCLUSION_COLUMNS)


AUDIT_COLUMNS = ["sample_id", "patient_id", "sample_type", "outcome", "removed_at_step", "step", "reason",
                 "patient still in cohort", "inflated at step", "rows before fix"]


def audit_trail_frame(result):
    """Every sample the run started from, once: kept, or the step that removed it and why; and, where a join
    counted it more than once before the fix, at which step and how many rows it had."""
    if result.start_frame is None:
        return pd.DataFrame(columns=AUDIT_COLUMNS)
    excl = exclusions_frame(result).drop_duplicates("sample_id").set_index("sample_id")
    in_cohort = set(result.cohort_ids)
    kept_patients = set(result.frame["patient_id"]) if result.cohort_ids else set()
    duplicated = {}
    for step, fix in sorted(fixes_by_step(result).items()):
        for sid, n in fix.get("rows_per_sample", {}).items():
            duplicated.setdefault(sid, (step, n))
    rows = []
    for _, x in result.start_frame.drop_duplicates("sample_id").iterrows():
        sid = x["sample_id"]
        removed = sid in excl.index and sid not in in_cohort
        dup_step, dup_rows = duplicated.get(sid, (None, None))
        rows.append({"sample_id": sid, "patient_id": x["patient_id"], "sample_type": x["sample_type"],
                     "outcome": "removed" if removed else ("in cohort" if sid in in_cohort else "not reached"),
                     "removed_at_step": excl.at[sid, "removed_at_step"] if removed else None,
                     "step": excl.at[sid, "step"] if removed else "",
                     "reason": excl.at[sid, "reason"] if removed else "",
                     "patient still in cohort": "yes" if x["patient_id"] in kept_patients else "no",
                     "inflated at step": dup_step, "rows before fix": dup_rows})
    frame = pd.DataFrame(rows, columns=AUDIT_COLUMNS)
    for col in ("removed_at_step", "inflated at step", "rows before fix"):
        frame[col] = frame[col].astype("Int64")
    return frame.sort_values(["removed_at_step", "sample_id"], na_position="first")


def cohort_frame(result):
    return pd.DataFrame({"sample_id": result.cohort_ids})


SELECTION_COLUMNS = ["step", "patient_id", "kept", "kept sample type", "dropped", "reason"]


def selection_frame(result):
    """Every patient for whom a 'one sample per patient' step had to choose: what it kept, what it dropped, and why."""
    rows = [{"step": s.index + 1, "patient_id": x["patient_id"], "kept": x["kept"], "kept sample type": x["kept_type"],
             "dropped": ", ".join(x["dropped"]), "reason": x["reason"]}
            for s in result.steps for x in s.selection]
    return pd.DataFrame(rows, columns=SELECTION_COLUMNS)


def coverage_frame(result, store):
    """Every clinical and survival variable, with how many cohort samples have a recorded value.

    Columns that start with '_' are UCSC Xena bookkeeping columns and are left out.
    """
    from .data import TABLE_FIELDS
    ids = list(result.cohort_ids)
    rows = []
    for table in ("clinical", "survival"):
        if not store.available(table):
            continue
        t = store.table(table).drop_duplicates("sample_id").set_index("sample_id")
        friendly = {store.resolve_field(table, f): f for f in TABLE_FIELDS[table]}
        sub = t.reindex(ids)
        for col in t.columns:
            if col in ("patient_id",) or col.startswith("_"):
                continue
            values = sub[col]
            recorded = values[~values.map(ops.is_missing)].astype(str)
            counts = recorded.value_counts()
            rows.append({"table": table, "column": col, "filter name": friendly.get(col, ""),
                         "recorded": int(len(recorded)),
                         "recorded %": round(100 * len(recorded) / len(ids), 1) if ids else 0.0,
                         "distinct values": int(counts.size),
                         "most common": f"{counts.index[0]} ({counts.iloc[0]})" if counts.size else ""})
    df = pd.DataFrame(rows, columns=["table", "column", "filter name", "recorded", "recorded %",
                                     "distinct values", "most common"])
    if df.empty:
        return df
    df["_catalogued"] = df["filter name"] != ""
    df = df.sort_values(["_catalogued", "recorded %", "column"], ascending=[False, False, True])
    return df.drop(columns="_catalogued").reset_index(drop=True)


def reproduce_script(result, store):
    """A standalone script that rebuilds the cohort with the same operations the app used."""
    spec = result.spec
    lines = [
        '"""Reproduce a Cohort Studio cohort from the frozen TCGA-BRCA release.',
        "",
        f"Request : {spec.get('request', '')}",
        f"Created : {datetime.date.today().isoformat()} by Cohort Studio",
        f"Expected: {len(result.cohort_ids)} samples",
        "",
        "Usage (from the repository root, after python data/tcga-brca/fetch.py):",
        "    python reproduce_cohort.py [data/tcga-brca] [cohort_samples.csv]",
        '"""',
        "import hashlib",
        "import sys",
        "",
        "# ---- the exact operations Cohort Studio uses (copied from cohort_studio/ops.py) ----",
        inspect.getsource(ops).split('"""', 2)[2].strip(),
        "",
        "",
        "def _sha256(path):",
        "    h = hashlib.sha256()",
        "    with open(path, 'rb') as f:",
        "        for chunk in iter(lambda: f.read(1 << 20), b''):",
        "            h.update(chunk)",
        "    return h.hexdigest()",
        "",
        "",
        "DATA = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path('data/tcga-brca')",
        "OUT = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else pathlib.Path('cohort_samples.csv')",
        "",
        "# Check the files are the ones this cohort was built from.",
        "EXPECTED = {",
    ]
    used = {"sample_manifest.csv": "sample_manifest.csv"}
    for s in spec["steps"]:
        if s["op"] == "join":
            if s["table"] == "slides":
                used["slide_manifest.csv"] = "slide_manifest.csv"
            if s["table"] in ("clinical", "survival"):
                used[f"{s['table']}.tsv"] = f"raw/{s['table']}.tsv"
        if s["op"] == "mutation":
            used["mutation_gene_level.tsv.gz"] = "raw/mutation_gene_level.tsv.gz"
        if s["op"] == "where":
            for name in condition_sources(s["condition"]):
                rel = {"slides": "slide_manifest.csv", "mutation": "raw/mutation_gene_level.tsv.gz",
                       "clinical": "raw/clinical.tsv", "survival": "raw/survival.tsv"}.get(name)
                if rel:
                    used[rel.split("/")[-1]] = rel
    for name, rel in used.items():
        digest = store.file_status.get(name, {}).get("sha256")
        if digest:
            lines.append(f"    {rel!r}: {digest!r},")
    lines += [
        "}",
        "for rel, digest in EXPECTED.items():",
        "    if _sha256(DATA / rel) != digest:",
        "        print(f'WARNING: {rel} differs from the file this cohort was built from')",
        "",
        "df = load_manifest(DATA)",
        "print(f'Start: {len(df)} rows, {df.sample_id.nunique()} samples')",
    ]
    loaded = set()
    for i, s in enumerate(spec["steps"], start=1):
        lines.append(f"\n# Step {i}: {describe_step(s)}")
        op = s["op"]
        if op == "sample_type":
            lines.append(f"df = keep_sample_types(df, {s['values']!r})")
        elif op == "layer":
            lines.append(f"df = require_layer(df, {s['layer']!r})")
        elif op == "join":
            t = s["table"]
            if t == "slides":
                lines.append(f"right = slides_of_kind(load_slides(DATA), {list(s.get('slide_kinds') or [])!r})")
            elif t == "normals" and s.get("require_layers"):
                lines.append(f"right = normal_samples_with_layers(load_manifest(DATA), {list(s['require_layers'])!r})")
            elif t == "normals":
                lines.append("right = normal_samples(load_manifest(DATA))")
            else:
                if t not in loaded:
                    lines.append(f"{t} = load_{t}(DATA)")
                    loaded.add(t)
                lines.append(f"right = {t}")
            lines.append(f"df = join_table(df, right, {PREFIX[t]!r}, {s['key']!r}, {s.get('mode', 'rows')!r})")
        elif op == "filter":
            col = store.resolve_field(s["table"], s["field"])
            if col is None:   # the file isn't downloaded or has no such column; the verdict already says so
                lines.append(f"raise SystemExit({('This release has no column for ' + repr(s['field']) + ' in ' + s['table'] + '; run data/tcga-brca/fetch.py first.')!r})")
            else:
                lines.append(f"df = filter_column(df, {PREFIX[s['table']] + '.' + col!r}, {s['cmp']!r}, {s.get('value')!r})")
        elif op == "mutation":
            if "mutation" not in loaded:
                lines.append("mutation = load_mutation(DATA)")
                loaded.add("mutation")
            lines.append(f"df = filter_mutation(df, mutation, {s['gene']!r}, {bool(s.get('mutated', True))!r})")
        elif op == "dedupe":
            lines.append("df = one_row_per_sample(df)")
        elif op == "where":
            names = sorted(condition_sources(s["condition"]))
            sources = ", ".join(f"{n!r}: load_{n}(DATA)" for n in names)
            tree = resolve_condition(s["condition"], store)
            lines.append(f"df = filter_where(df, {tree!r}, {{{sources}}}, {s.get('apply_to', 'sample')!r})")
        elif op == "one_per_patient":
            rule = s.get("rule", "priority")
            time_col = (f"{PREFIX['clinical']}.{store.resolve_field('clinical', 'collection_day')}"
                        if rule in ("earliest", "latest") else None)
            lines.append(f"df = one_sample_per_patient(df, {rule!r}, {s.get('order')!r}, {time_col!r})")
        elif op == "exclude_layer":
            lines.append(f"df = exclude_layer(df, {s['layer']!r})")
        elif op == "any_of":
            conds = [(PREFIX[s["table"]] + "." + store.resolve_field(s["table"], c["field"]), c["cmp"], c.get("value"))
                     for c in s["conditions"]]
            lines.append(f"df = filter_any(df, {conds!r})")
        lines.append(f"print(f'Step {i}: {{len(df)}} rows, {{df.sample_id.nunique()}} samples, {{df.patient_id.nunique()}} patients')")
    lines += [
        "",
        f"ids = cohort_sample_ids(df, {extra_cohort_columns(spec)!r})",
        "pd.DataFrame({'sample_id': ids}).to_csv(OUT, index=False)",
        f"print(f'Wrote {{len(ids)}} samples to {{OUT}} (expected {len(result.cohort_ids)})')",
        "",
    ]
    return "\n".join(lines)


CHECK_KINDS = {"Filter fields are recorded for most samples": "missing_data",
               "The outcome you named is recorded for the cohort": "missing_data",
               "One sample per patient": "duplicate",
               "Files match the event's frozen checksums": "membership_change"}


def findings_document(result, verdict, store, team="Cohort Studio team"):
    """The shared findings format (v0.2), so Evidence Navigator tools can read our checks."""
    files = []
    for name, st in store.file_status.items():
        if st.get("sha256"):
            files.append({"file": name, "sha256": st["sha256"]})
    findings = []
    n = 0
    for s in result.steps:
        if s.inflated:
            n += 1
            ev = [{"kind": "sample", "id": ex["sample_id"], "note": f"{ex['rows']} rows"
                   + (f"; matched {', '.join(ex.get('matched', []))}" if ex.get("matched") else "")}
                  for ex in s.duplicate_examples] or [{"kind": "analysis", "id": f"step-{s.index + 1}"}]
            fixed = not result.unresolved_inflation
            findings.append({"id": f"F{n}", "kind": "join_inflation",
                             "summary": f"Step {s.index + 1} ({s.label}): {s.inflation_note}",
                             "severity": "low" if fixed else "high", "confidence": "high", "evidence": ev,
                             "limitation": "Resolved by a later step." if fixed else "Unresolved: the export would count samples twice.",
                             "needs_human_review": not fixed})
    for fix in result.spec.get("fix_log", []):
        n += 1
        ev = [{"kind": "sample", "id": ex["sample_id"], "note": f"{ex['rows']} rows"
               + (f"; matched {', '.join(ex.get('matched', []))}" if ex.get("matched") else "")}
              for ex in fix.get("examples", [])] or [{"kind": "analysis", "id": f"step-{fix['step']}"}]
        findings.append({"id": f"F{n}", "kind": "join_inflation",
                         "summary": (f"Step {fix['step']} ({fix['was']}) produced {fix['rows']:,} rows for "
                                     f"{fix['samples']:,} distinct samples. Fixed: {fix['fix']}"),
                         "severity": "low", "confidence": "high", "evidence": ev,
                         "limitation": "Fixed before export; the cohort file has one row per sample.",
                         "needs_human_review": False})
    for s in result.steps:
        big = {k: v for k, v in s.shift.items()
               if k != "Sample type" and not v.get("expected") and is_flagged_shift(v)}
        if big:
            n += 1
            var, info = max(big.items(), key=lambda kv: kv[1]["tvd"])
            findings.append({"id": f"F{n}", "kind": "missing_data",
                             "summary": (f"Step {s.index + 1} ({s.label}) removed {len(s.removed)} samples and shifted "
                                         f"{var} by {info['tvd']:.2f}: {info['group']} went from {info['before_pct']}% "
                                         f"to {info['after_pct']}%."),
                             "severity": "medium", "confidence": "medium",
                             "evidence": [{"kind": "sample", "id": sid} for sid in s.removed[:10]],
                             "limitation": "The shift shows who was removed, not why their data is missing.",
                             "needs_human_review": True})
    # Every other check that didn't pass gets a finding too, so findings.json and the verdict always agree.
    covered = {"No sample is counted twice"} | ({"The cohort still looks like the population it started from"}
                                               if any(f["kind"] == "missing_data" for f in findings) else set())
    for c in verdict.checks:
        if c.status == "pass" or c.name in covered:
            continue
        n += 1
        findings.append({"id": f"F{n}", "kind": CHECK_KINDS.get(c.name, "other"),
                         "summary": f"{PROBLEM_HEADLINES.get(c.name, c.name)}. {c.detail}",
                         "severity": {"fail": "high", "unknown": "medium", "warn": "medium"}[c.status],
                         "confidence": "medium",
                         "evidence": [{"kind": "file", "id": f} for f in c.fields if f.endswith((".csv", ".tsv", ".gz"))]
                         or [{"kind": "analysis", "id": "verdict-check"}],
                         "limitation": c.review or "See verdict.json for the check.",
                         "needs_human_review": True})
    if not findings:
        findings.append({"id": "F1", "kind": "other", "summary": "Every check passed: no join inflation, no composition shift beyond chance, and no other problem was found.",
                         "severity": "none", "confidence": "medium",
                         "evidence": [{"kind": "file", "id": "sample_manifest.csv"}], "needs_human_review": False})
    return {
        "format_version": "0.2",
        "producer": {"team": team, "product": "Cohort Studio", "track": "cohort-firewall"},
        "data": {"dataset": "TCGA-BRCA", "releases": [{"name": "frozen event release", "files": files}]},
        "question": result.spec.get("request") or "Cohort definition",
        "verdict": verdict.verdict,
        "findings": findings,
    }


def environment():
    """Python and package versions, plus the code's git commit when known, so a rebuild can match them."""
    import platform
    import subprocess
    from importlib import metadata
    env = {"python": platform.python_version()}
    for pkg in ("pandas", "numpy"):
        try:
            env[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            pass
    try:
        root = pathlib.Path(__file__).resolve().parent.parent
        commit = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True,
                                text=True, timeout=5).stdout.strip()
        if commit:
            env["cohort_studio_commit"] = commit
    except Exception:
        pass
    return env


def write_all(result, verdict, store, out_dir, team="Cohort Studio team"):
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cohort_frame(result).to_csv(out / "cohort_samples.csv", index=False)
    result.spec["environment"] = environment()
    (out / "cohort_spec.json").write_text(json.dumps(result.spec, indent=2))
    ledger_frame(result).to_csv(out / "ledger.csv", index=False)
    exclusions_frame(result).to_csv(out / "exclusions.csv", index=False)
    audit_trail_frame(result).to_csv(out / "audit_trail.csv", index=False)
    selection_frame(result).to_csv(out / "sample_selection.csv", index=False)
    coverage_frame(result, store).to_csv(out / "coverage.csv", index=False)
    (out / "verdict.json").write_text(json.dumps(verdict.to_dict(), indent=2))
    (out / "reproduce_cohort.py").write_text(reproduce_script(result, store))
    (out / "findings.json").write_text(json.dumps(findings_document(result, verdict, store, team), indent=2))
    return out
