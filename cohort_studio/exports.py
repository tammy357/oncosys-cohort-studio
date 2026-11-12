"""Write the outputs: cohort_samples.csv, a reproduce script, the spec, the ledger, the verdict and findings.json."""
import datetime
import inspect
import json
import pathlib

import pandas as pd

from . import ops
from .engine import PREFIX, extra_cohort_columns
from .spec import describe_step


def ledger_frame(result):
    rows = []
    for s in result.steps:
        top = max(s.shift.items(), key=lambda kv: kv[1]["tvd"], default=(None, None))
        rows.append({
            "step": s.index + 1, "what it does": s.label,
            "rows": s.rows_out if not s.skipped else None,
            "samples": s.samples_out if not s.skipped else None,
            "patients": s.patients_out if not s.skipped else None,
            "samples removed": len(s.removed),
            "inflated": "YES" if s.inflated else "",
            "largest shift": f"{top[0]} {top[1]['tvd']:.2f}" if top[0] else "",
            "error": s.error,
        })
    return pd.DataFrame(rows)


def exclusions_frame(result):
    rows = [{"sample_id": sid, "removed_at_step": s.index + 1, "step": s.label, "reason": s.removed_reason}
            for s in result.steps for sid in s.removed]
    return pd.DataFrame(rows, columns=["sample_id", "removed_at_step", "step", "reason"])


def cohort_frame(result):
    return pd.DataFrame({"sample_id": result.cohort_ids})


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
            lines.append(f"df = filter_column(df, {PREFIX[s['table']] + '.' + col!r}, {s['cmp']!r}, {s.get('value')!r})")
        elif op == "mutation":
            if "mutation" not in loaded:
                lines.append("mutation = load_mutation(DATA)")
                loaded.add("mutation")
            lines.append(f"df = filter_mutation(df, mutation, {s['gene']!r}, {bool(s.get('mutated', True))!r})")
        elif op == "dedupe":
            lines.append("df = one_row_per_sample(df)")
        lines.append(f"print(f'Step {i}: {{len(df)}} rows, {{df.sample_id.nunique()}} samples, {{df.patient_id.nunique()}} patients')")
    lines += [
        "",
        f"ids = cohort_sample_ids(df, {extra_cohort_columns(spec)!r})",
        "pd.DataFrame({'sample_id': ids}).to_csv(OUT, index=False)",
        f"print(f'Wrote {{len(ids)}} samples to {{OUT}} (expected {len(result.cohort_ids)})')",
        "",
    ]
    return "\n".join(lines)


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
        big = {k: v for k, v in s.shift.items() if v["tvd"] >= 0.10 and k != "Sample type"}
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
    if not findings:
        findings.append({"id": "F1", "kind": "other", "summary": "No join inflation or composition shift above 0.10 was found.",
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


def write_all(result, verdict, store, out_dir, team="Cohort Studio team"):
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cohort_frame(result).to_csv(out / "cohort_samples.csv", index=False)
    (out / "cohort_spec.json").write_text(json.dumps(result.spec, indent=2))
    ledger_frame(result).to_csv(out / "ledger.csv", index=False)
    exclusions_frame(result).to_csv(out / "exclusions.csv", index=False)
    (out / "verdict.json").write_text(json.dumps(verdict.to_dict(), indent=2))
    (out / "reproduce_cohort.py").write_text(reproduce_script(result, store))
    (out / "findings.json").write_text(json.dumps(findings_document(result, verdict, store, team), indent=2))
    return out
