"""Cohort Studio: build a TCGA-BRCA cohort in plain English and catch joins that count samples twice.

Run from the repository root:   streamlit run app.py
"""
import io
import json
import pathlib

import matplotlib
import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from cohort_studio import engine, exports, verdict
from cohort_studio.data import DataStore
from cohort_studio.editor import COLUMNS, STEP_TYPES, steps_to_table, table_to_steps
from cohort_studio.parse import parse_request
from cohort_studio.spec import describe_step, new_spec

matplotlib.use("Agg")
st.set_page_config(page_title="Cohort Studio", layout="wide")

EXAMPLES = [
    "primary tumors with both RNA and protein data and a diagnostic slide",
    "ER-positive primary tumors with a PAM50 subtype and frozen slides",
    "basal-like tumors with progression-free interval data, joined by patient",
    "tumor-normal pairs with RNA",
]
STATUS_LABEL = {"pass": "PASS", "warn": "WARNING", "fail": "FAIL", "unknown": "CAN'T TELL"}


@st.cache_resource
def get_store(data_dir):
    return DataStore(data_dir)


def set_spec(spec):
    st.session_state.spec = spec
    st.session_state.editor_version = st.session_state.get("editor_version", 0) + 1
    st.session_state.pop("result", None)


def run_spec(spec, store):
    result = engine.run(spec, store)
    st.session_state.spec = spec
    st.session_state.result = result
    st.session_state.verdict = verdict.evaluate(result, store)


def attrition_chart(result):
    labels = ["All samples"] + [f"Step {s.index + 1}" for s in result.steps]
    samples = [len(result.start_samples)] + [s.samples_out if not s.skipped else 0 for s in result.steps]
    rows = [len(result.start_samples)] + [s.rows_out if not s.skipped else 0 for s in result.steps]
    fig, ax = plt.subplots(figsize=(7, 0.42 * len(labels) + 1.0))
    y = range(len(labels))
    ax.barh(y, samples, color="#4C72B0", label="distinct samples")
    for i, (n, r) in enumerate(zip(samples, rows)):
        if r > n:
            ax.plot([r], [i], marker="|", markersize=16, color="#C44E52", mew=2.5)
            ax.annotate(f" {r} rows", (r, i), va="center", color="#C44E52", fontsize=9)
        else:
            ax.annotate(f" {n}", (n, i), va="center", fontsize=9)
    ax.set_yticks(list(y))
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel("Samples remaining (red tick: rows, where a join counts samples more than once)", fontsize=9)
    ax.set_xlim(0, max(rows) * 1.18)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return fig


# ---------------- sidebar ----------------
with st.sidebar:
    st.header("Data")
    data_dir = st.text_input("Release folder", "data/tcga-brca", key="data_dir")
    try:
        store = get_store(data_dir)
    except Exception as exc:
        st.error(f"Couldn't read {data_dir}: {exc}")
        st.stop()
    for name, info in store.file_status.items():
        mark = "✓" if info["present"] and info["verified"] is not False else ("✗" if info["verified"] is False else "–")
        st.caption(f"{mark} {name}: {info['note']}")
    if not store.available("clinical"):
        st.warning("Clinical, survival and mutation files aren't downloaded. Run `python data/tcga-brca/fetch.py --no-rna`, "
                   "then restart. Manifest-only cohorts (sample types, data layers, slides) still work.")

    st.header("AI (optional)")
    use_ai = st.toggle("Draft steps with an AI model", value=False, key="use_ai",
                       help="Off by default. The built-in parser needs no key. The AI only drafts the steps; "
                            "every count comes from the data.")
    provider = st.selectbox("Provider", ["anthropic", "openai"], key="provider",
                            help="'openai' works with any OpenAI-compatible endpoint, including a local model.")
    api_key = st.text_input("Your API key (not stored)", type="password", key="api_key")
    model = st.text_input("Model (optional)", key="model")
    base_url = st.text_input("Base URL (OpenAI-compatible, optional)", key="base_url",
                             help="For a local model, e.g. http://localhost:11434/v1 for Ollama.")

    st.header("Export")
    team = st.text_input("Team name (for findings.json)", "Cohort Studio team", key="team")

# ---------------- header ----------------
st.title("Cohort Studio")
st.write("Describe the TCGA-BRCA cohort you need. Check and edit the steps, see how many samples and patients "
         "survive each one, catch any join that counts a sample twice, then export the cohort and a script that "
         "rebuilds it.")
st.caption("Research use only. This tool makes no diagnosis, treatment recommendation or clinical claim.")

# ---------------- 1. request ----------------
st.subheader("1 · Your request")
cols = st.columns(len(EXAMPLES))
for c, ex in zip(cols, EXAMPLES):
    if c.button(ex, key=f"ex_{ex}", width="stretch"):
        st.session_state.request = ex
request = st.text_area("Cohort in plain English", key="request", height=70,
                       placeholder="e.g. primary tumors with both RNA and protein data and a diagnostic slide")
if st.button("Draft the steps", type="primary", disabled=not request.strip()):
    spec = None
    if use_ai:
        from cohort_studio.llm import AIUnavailable, ai_parse
        try:
            spec, problems = ai_parse(request, store, provider=provider, api_key=api_key or None,
                                      model=model or None, base_url=base_url or None)
            if problems:
                st.warning("The AI draft has problems, so the built-in parser was used instead: "
                           + "; ".join(m for _, m in problems))
                spec = None
        except AIUnavailable as exc:
            st.warning(str(exc))
    set_spec(spec or parse_request(request, store))

spec = st.session_state.get("spec")
if not spec:
    st.info("Pick an example or type a request, then select **Draft the steps**.")
    st.stop()

# ---------------- 2. steps ----------------
st.subheader("2 · Check and edit the steps")
st.caption(f"Drafted by: {'the built-in parser' if spec.get('parser') == 'rules' else spec.get('parser')}. "
           "Nothing runs until you select **Run the steps**.")
if spec.get("understood"):
    st.write("**I read your request as:** " + " · ".join(f"“{u['phrase']}” → {u['meaning']}" for u in spec["understood"]))
for note in spec.get("notes", []):
    st.info(note)
if spec.get("unparsed"):
    st.warning(f"These words weren't turned into steps: **{', '.join(spec['unparsed'])}**. "
               "Add a step for them, or confirm they don't change the cohort.")
    spec["unparsed_confirmed"] = st.checkbox("I've checked: these words don't change the cohort",
                                             value=bool(spec.get("unparsed_confirmed")),
                                             key=f"confirm_{st.session_state.get('editor_version', 0)}")

edited = st.data_editor(
    steps_to_table(spec["steps"]), key=f"editor_{st.session_state.get('editor_version', 0)}",
    num_rows="dynamic", width="stretch", hide_index=False,
    column_config={
        "step": st.column_config.SelectboxColumn("step", options=STEP_TYPES, required=True),
        "table": st.column_config.SelectboxColumn("table", options=["", "clinical", "survival", "slides", "normals"]),
        "key": st.column_config.SelectboxColumn("key", options=["", "sample_id", "patient_id"]),
        "mode": st.column_config.SelectboxColumn("mode", options=["", "rows", "exists"],
                                                 help="rows = one row per match; exists = keep each sample once"),
        "compare": st.column_config.SelectboxColumn("compare", options=["", "in", "not_in", "notnull", "gte", "lte",
                                                                         "between", "stage_in", "contains", "is_event"]),
        "add to cohort": st.column_config.CheckboxColumn("add to cohort"),
    })
with st.expander("What each column means"):
    st.markdown(
        "- **sample_type**: `what` = Primary Tumor, Solid Tissue Normal, Metastatic (comma-separated)\n"
        "- **layer**: `what` = rna, protein, mutation, copy_number, survival, clinical\n"
        "- **join**: `table` = clinical, survival, slides or normals; `key` = sample_id or patient_id; "
        "`mode` = rows (one row per match) or exists (keep each sample once); `slide kinds` = diagnostic (FFPE), "
        "frozen, top section, ... or any\n"
        "- **filter**: `table` + `what` = field (er_status, pr_status, her2_status, pam50, stage, age, histology, "
        "gender, menopause; survival: os, pfi, dss, dfi); `compare` and `value`, e.g. in / Positive, between / 40-60\n"
        "- **mutation**: `what` = gene, `value` = mutated or not mutated\n"
        "- **dedupe**: keep one row per sample")

if st.button("Run the steps", type="primary"):
    steps, problems = table_to_steps(edited)
    if problems:
        st.error(" ".join(problems))
    else:
        new = dict(spec)
        new["steps"] = steps
        run_spec(new, store)

result = st.session_state.get("result")
if result is None:
    st.stop()
v = st.session_state.verdict

# ---------------- 3. ledger ----------------
st.subheader("3 · What each step did")
st.dataframe(exports.ledger_frame(result), width="stretch", hide_index=True)
chart_col, _ = st.columns([3, 2])
with chart_col:
    st.pyplot(attrition_chart(result))

for s in result.steps:
    if s.error:
        st.error(f"Step {s.index + 1} didn't run: {s.error}")
for s in [s for s in result.steps if s.inflated]:
    with st.container(border=True):
        st.markdown(f"**Step {s.index + 1} counts samples more than once.** {s.inflation_note}")
        ex = pd.DataFrame([{"sample_id": e["sample_id"], "rows": e["rows"], "matched": ", ".join(e.get("matched", []))}
                           for e in s.duplicate_examples])
        if not ex.empty:
            st.caption("Examples of samples that now appear more than once:")
            st.dataframe(ex, hide_index=True, width="stretch")
        fixed_step, why = engine.suggest_fix(result.spec["steps"][s.index])
        st.write(f"Suggested fix: {why}")
        if st.button(f"Apply this fix to step {s.index + 1}", key=f"fix_{s.index}"):
            new, _ = engine.apply_fixes(result.spec, result, only=s.index)
            set_spec(new)
            run_spec(new, store)
            st.rerun()
for f in result.spec.get("fix_log", []):
    st.success(f"Fixed step {f['step']}: it had {f['rows']:,} rows for {f['samples']:,} samples. {f['fix']}")

# ---------------- 4. who was removed ----------------
st.subheader("4 · Who did the steps remove?")
st.caption("Total variation distance (TVD): 0 means the mix of a group is unchanged, 1 means no overlap. "
           f"A step that moves any mix by {verdict.SHIFT_WARN} or more is flagged. Fields you filtered on are left out.")
shift_rows = []
for s in result.steps:
    for var, info in s.shift.items():
        if var == "Sample type":
            continue
        shift_rows.append({"step": s.index + 1, "what it does": s.label, "samples removed": len(s.removed),
                           "characteristic": var, "TVD": info["tvd"],
                           "largest change": f"{info['group']}: {info['before_pct']}% → {info['after_pct']}%",
                           "flag": "SHIFT" if info["tvd"] >= verdict.SHIFT_WARN else ""})
if shift_rows:
    sdf = pd.DataFrame(shift_rows).sort_values("TVD", ascending=False)
    st.dataframe(sdf, width="stretch", hide_index=True)
excl = exports.exclusions_frame(result)
with st.expander(f"Every excluded sample, with the reason ({len(excl):,})"):
    st.dataframe(excl, width="stretch", hide_index=True)

# ---------------- 5. verdict ----------------
st.subheader("5 · Verdict")
with st.container(border=True):
    st.markdown(f"### {v.verdict}")
    st.write(v.reason)
    st.dataframe(pd.DataFrame([{"result": STATUS_LABEL[c.status], "check": c.name, "detail": c.detail}
                               for c in v.checks]), width="stretch", hide_index=True)
    st.markdown("**Before anyone uses this cohort, a person should:**\n" + "\n".join(f"- {r}" for r in v.human_review))
    st.caption(f"Decision rule: {v.decision_rule}")
with st.container(border=True):
    st.markdown("**Limitations**")
    st.markdown("\n".join(f"- {l}" for l in v.limitations))

# ---------------- 6. export ----------------
st.subheader("6 · Export")
if not result.ok:
    st.error("Fix the steps that didn't run before exporting.")
elif result.unresolved_inflation:
    st.error("Export is paused: the cohort still counts some samples more than once. Apply the fix above first.")
else:
    files = {
        "cohort_samples.csv": exports.cohort_frame(result).to_csv(index=False),
        "reproduce_cohort.py": exports.reproduce_script(result, store),
        "cohort_spec.json": json.dumps(result.spec, indent=2),
        "findings.json": json.dumps(exports.findings_document(result, v, store, team), indent=2),
        "ledger.csv": exports.ledger_frame(result).to_csv(index=False),
        "exclusions.csv": excl.to_csv(index=False),
    }
    st.write(f"**{len(result.cohort_ids):,} samples** from {result.final_patients:,} patients.")
    cols = st.columns(3)
    for i, (name, content) in enumerate(files.items()):
        cols[i % 3].download_button(f"Download {name}", content, file_name=name, key=f"dl_{name}",
                                    width="stretch")
    if st.button("Save all files to the output/ folder"):
        out = exports.write_all(result, v, store, "output", team=team)
        st.success(f"Saved to {out.resolve()}")
    with st.expander("Check it yourself: the steps of the script that rebuilds this cohort"):
        script = files["reproduce_cohort.py"]
        marker = "df = load_manifest(DATA)"
        st.caption("The downloaded script also contains the helper functions it calls (copied from cohort_studio/ops.py) "
                   "and checks that your files match the ones used here. Run: python reproduce_cohort.py")
        st.code(marker + script.split(marker, 1)[1] if marker in script else script, language="python")

    # stretch: save and compare cohort versions
    st.subheader("7 · Compare cohort versions")
    versions = st.session_state.setdefault("versions", [])
    name = st.text_input("Name this version", value=f"Version {len(versions) + 1}", key="version_name")
    if st.button("Save this version"):
        versions.append({"name": name, "request": result.spec.get("request", ""), "ids": list(result.cohort_ids),
                         "steps": [describe_step(s) for s in result.spec["steps"]]})
    if len(versions) >= 2:
        a_name = st.selectbox("Version A", [x["name"] for x in versions], index=0, key="cmp_a")
        b_name = st.selectbox("Version B", [x["name"] for x in versions], index=len(versions) - 1, key="cmp_b")
        a = next(x for x in versions if x["name"] == a_name)
        b = next(x for x in versions if x["name"] == b_name)
        sa, sb = set(a["ids"]), set(b["ids"])
        c1, c2, c3 = st.columns(3)
        c1.metric("Only in A", len(sa - sb))
        c2.metric("In both", len(sa & sb))
        c3.metric("Only in B", len(sb - sa))
        st.dataframe(pd.DataFrame({"A steps": pd.Series(a["steps"]), "B steps": pd.Series(b["steps"])}),
                     width="stretch", hide_index=True)
    elif versions:
        st.caption("Save one more version to compare them.")
