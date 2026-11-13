"""Data operations shared by the Cohort Studio engine and the scripts it exports.

Everything here uses only pandas and the standard library, because the exported
reproduce script copies these functions word for word. That way the export
reproduces the cohort with exactly the same logic the app used.
"""
import pathlib
import re

import pandas as pd

LAYER_COLUMNS = {
    "clinical": "in_clinical",
    "survival": "has_survival",
    "mutation": "has_mutation",
    "copy_number": "has_copy_number",
    "rna": "has_rna",
    "protein": "has_protein",
}

SAMPLE_TYPES = ["Primary Tumor", "Solid Tissue Normal", "Metastatic"]
SLIDE_KINDS = ["diagnostic (FFPE)", "frozen, top section", "frozen, middle section", "frozen, bottom section"]
FROZEN_KINDS = [k for k in SLIDE_KINDS if k.startswith("frozen")]

MISSING_TOKENS = {"", "nan", "na", "n/a", "none", "null", "unknown", "not available", "not evaluated",
                  "indeterminate", "equivocal", "not reported", "not performed", "performed but not available"}


def first_column(df, names):
    """Return the first of `names` that is a column of df, or None."""
    for name in names:
        if name in df.columns:
            return name
    return None


def is_missing(value):
    """TCGA marks missing values in several ways, e.g. '[Not Available]' or 'Indeterminate'."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return True
    text = str(value).strip()
    return text.startswith("[") or text.lower() in MISSING_TOKENS


def load_manifest(data_dir):
    m = pd.read_csv(pathlib.Path(data_dir) / "sample_manifest.csv")
    for col in list(LAYER_COLUMNS.values()):
        m[col] = m[col].astype(str).str.strip().str.lower().eq("true")
    m["slides_for_sample"] = pd.to_numeric(m["slides_for_sample"], errors="coerce").fillna(0).astype(int)
    return m


def load_slides(data_dir):
    s = pd.read_csv(pathlib.Path(data_dir) / "slide_manifest.csv")
    return s[["slide_barcode", "sample_id", "patient_id", "slide_kind"]].copy()


def _load_keyed_table(path):
    t = pd.read_csv(path, sep="\t", low_memory=False, dtype=str)
    sid = first_column(t, ["sampleID", "sample", "sample_id", "Sample"])
    if sid is None:
        raise ValueError(f"{path.name}: no sample ID column found")
    t = t.rename(columns={sid: "sample_id"})
    t["sample_id"] = t["sample_id"].astype(str).str[:15]
    pid = first_column(t, ["_PATIENT", "patient_id", "bcr_patient_barcode"])
    t["patient_id"] = t[pid].astype(str).str[:12] if pid else t["sample_id"].str[:12]
    return t


def load_clinical(data_dir):
    return _load_keyed_table(pathlib.Path(data_dir) / "raw" / "clinical.tsv")


def load_survival(data_dir):
    return _load_keyed_table(pathlib.Path(data_dir) / "raw" / "survival.tsv")


def load_mutation(data_dir):
    m = pd.read_csv(pathlib.Path(data_dir) / "raw" / "mutation_gene_level.tsv.gz", sep="\t", index_col=0)
    m.columns = [str(c)[:15] for c in m.columns]
    return m


def normal_samples(manifest):
    n = manifest[manifest["sample_type"] == "Solid Tissue Normal"]
    return n[["sample_id", "patient_id"]].copy()


def slides_of_kind(slides, kinds):
    if not kinds:
        return slides.copy()
    return slides[slides["slide_kind"].isin(kinds)].copy()


def keep_sample_types(df, values):
    return df[df["sample_type"].isin(values)].copy()


def require_layer(df, layer):
    return df[df[LAYER_COLUMNS[layer]]].copy()


def join_table(df, right, prefix, key, mode):
    """Join `right` onto df by `key` ('sample_id' or 'patient_id').

    mode 'rows'   : an ordinary inner join; one output row per matching right-hand row,
                    so a sample with three matching slides becomes three rows.
    mode 'exists' : keep each left row once if it has at least one match (a semi-join),
                    and record how many matches it had.
    """
    # Joining a table again replaces only the columns this join creates, so names never clash (e.g. clinical
    # joined by sample, then by patient) and other columns survive (e.g. a pairs join's normal.sample_id).
    new = {f"{prefix}.{c}" for c in right.columns} if mode == "rows" else {f"{prefix}.n_matches"}
    df = df.drop(columns=[c for c in df.columns if c in new])
    r = right.copy()
    r.columns = [f"{prefix}.{c}" for c in r.columns]
    rkey = f"{prefix}.{key}"
    if mode == "rows":
        return df.merge(r, left_on=key, right_on=rkey, how="inner")
    counts = r[rkey].value_counts()
    out = df[df[key].isin(counts.index)].copy()
    out[f"{prefix}.n_matches"] = out[key].map(counts).astype(int)
    return out


def stage_group(value):
    """'Stage IIA' -> 'II'. Returns None when the stage is missing."""
    if is_missing(value):
        return None
    match = re.search(r"stage\s*(iv|iii|ii|i)(?![v])", str(value).lower())
    return match.group(1).upper() if match else None


def filter_column(df, column, cmp, value):
    """Filter rows of df on one column. Missing values never pass a filter."""
    col = df[column]
    if cmp == "notnull":
        keep = ~col.map(is_missing)
    elif cmp in ("in", "not_in"):
        wanted = {str(v).strip().lower() for v in value}
        hit = col.map(lambda x: (not is_missing(x)) and str(x).strip().lower() in wanted)
        keep = hit if cmp == "in" else (~hit & ~col.map(is_missing))
    elif cmp == "contains":
        wanted = [str(v).strip().lower() for v in value]
        keep = col.map(lambda x: (not is_missing(x)) and any(w in str(x).lower() for w in wanted))
    elif cmp == "stage_in":
        wanted = {str(v).upper() for v in value}
        keep = col.map(lambda x: stage_group(x) in wanted)
    elif cmp in ("gte", "lte", "between"):
        num = pd.to_numeric(col, errors="coerce")
        if cmp == "gte":
            keep = num >= float(value)
        elif cmp == "lte":
            keep = num <= float(value)
        else:
            keep = (num >= float(value[0])) & (num <= float(value[1]))
        keep = keep.fillna(False)
    elif cmp == "is_event":
        keep = pd.to_numeric(col, errors="coerce") == 1
    elif cmp == "in_raw":
        # Exact raw values, including ones is_missing() treats as missing (e.g. HER2 'Equivocal').
        wanted = {str(v).strip().lower() for v in value}
        keep = col.map(lambda x: x is not None and not (isinstance(x, float) and pd.isna(x))
                       and str(x).strip().lower() in wanted)
    else:
        raise ValueError(f"Unknown comparison '{cmp}'")
    return df[keep.astype(bool)].copy()


def filter_mutation(df, mutation, gene, mutated):
    """Keep samples whose gene-level mutation call matches. Samples absent from the file are dropped."""
    if gene not in mutation.index:
        raise ValueError(f"Gene '{gene}' is not in the mutation file")
    calls = pd.to_numeric(mutation.loc[gene], errors="coerce")
    assessed = df["sample_id"].isin(calls.index)
    status = df["sample_id"].map(calls)
    keep = assessed & ((status > 0) if mutated else (status == 0))
    return df[keep.fillna(False).astype(bool)].copy()


def one_row_per_sample(df):
    return df.drop_duplicates(subset=["sample_id"]).copy()


SAMPLE_TYPE_PRIORITY =["Primary Tumor", "Metastatic", "Solid Tissue Normal"]


def one_sample_per_patient(df, rule="priority", order=None, time_column=None):
    """Keep one sample per patient, chosen by a stated rule, and say why in a 'selection.reason' column.

    rule 'priority'          : the first sample type in `order` (default: primary tumor, then
                               metastatic, then normal), then the lowest sample barcode.
    rule 'earliest'/'latest' : the smallest / largest value in `time_column` (days from diagnosis
                               to sample collection). Samples with no recorded day come last, and
                               ties fall back to the priority rule.
    Every row of the kept sample stays, so earlier joins keep their rows.
    """
    order = list(order or SAMPLE_TYPE_PRIORITY)
    cols = ["sample_id", "patient_id", "sample_type"] + ([time_column] if time_column else [])
    per = df.drop_duplicates("sample_id")[cols].copy()
    per["_rank"] = per["sample_type"].map(lambda t: order.index(t) if t in order else len(order))
    by = ["_rank", "sample_id"]
    if rule in ("earliest", "latest"):
        per["_day"] = pd.to_numeric(per[time_column], errors="coerce")
        per["_sort_day"] = per["_day"] if rule == "earliest" else -per["_day"]
        by = ["_sort_day"] + by
    per = per.sort_values(by, na_position="last")
    counts = per.groupby("patient_id").size()
    kept = per.drop_duplicates("patient_id")
    reasons = {}
    for _, row in kept.iterrows():
        n = int(counts[row["patient_id"]])
        if n == 1:
            reasons[row["sample_id"]] = "the patient's only sample at this step"
        elif rule in ("earliest", "latest") and not pd.isna(row["_day"]):
            reasons[row["sample_id"]] = f"{rule} collection (day {row['_day']:g}) of the patient's {n} samples"
        elif rule in ("earliest", "latest"):
            reasons[row["sample_id"]] = (f"no collection day recorded for the patient's {n} samples, "
                                         f"so kept by sample type ({' > '.join(order)})")
        else:
            reasons[row["sample_id"]] = f"first by sample type ({' > '.join(order)}) of the patient's {n} samples"
    out = df[df["sample_id"].isin(reasons)].copy()
    out["selection.reason"] = out["sample_id"].map(reasons)
    return out


# ---- steps used by the AI assistant and the advanced search (the rule parser never writes these) ----
def exclude_layer(df, layer):
    """Keep samples that do NOT have this data layer (e.g. 'RNA but no protein data')."""
    return df[~df[LAYER_COLUMNS[layer]]].copy()


def normal_samples_with_layers(manifest, layers):
    """Normal-tissue samples that also have every data layer in `layers`."""
    n = manifest[manifest["sample_type"] == "Solid Tissue Normal"]
    for layer in layers or []:
        n = n[n[LAYER_COLUMNS[layer]]]
    return n[["sample_id", "patient_id"]].copy()


def filter_any(df, conditions):
    """Keep rows that meet at least one condition. conditions: [(column, cmp, value), ...]."""
    keep = pd.Series(False, index=df.index)
    for column, cmp, value in conditions:
        keep |= df.index.isin(filter_column(df, column, cmp, value).index)
    return df[keep].copy()


# ---- conditions: AND / OR / NOT across data layers, sample types, genes, slides and clinical/survival fields ----
def evaluate_condition(node, ids, sources):
    """How a condition holds for each sample ID: 1.0 true, 0.0 false, NaN unknown.

    Unknown means a value the test needs is not recorded (or the sample has no mutation calls). AND, OR and NOT
    use three-valued logic, so an unknown can still be decided (false AND unknown = false, true OR unknown = true);
    NOT unknown stays unknown, and a cohort keeps only samples where the whole condition is true.

    node    : {"all": [...]} | {"any": [...]} | {"not": node} | one test:
              {"layer": L} | {"sample_type": [...]} | {"gene": G, "mutated": true/false} | {"slide_kinds": [...]}
              ([] = any slide) | {"table": "clinical"|"survival", "column": <real column>, "cmp": ..., "value": ...}
    ids     : sample IDs (a list or Series)
    sources : the loaded tables a test needs: "manifest", "slides", "mutation", "clinical", "survival"
    Each sample is looked up by its own ID, so a condition never adds rows.
    """
    ids = pd.Series(list(ids), dtype=object)
    group = "all" if "all" in node else "any" if "any" in node else None
    if group:
        parts = pd.concat([evaluate_condition(k, ids, sources) for k in node[group]], axis=1)
        unknown = parts.isna().any(axis=1)
        out = pd.Series(1.0 if group == "all" else 0.0, index=ids.index)
        out[unknown] = float("nan")
        if group == "all":
            out[parts.eq(0.0).any(axis=1)] = 0.0
        else:
            out[parts.eq(1.0).any(axis=1)] = 1.0
        return out
    if "not" in node:
        return 1.0 - evaluate_condition(node["not"], ids, sources)
    if "layer" in node or "sample_type" in node:
        manifest = sources["manifest"].drop_duplicates("sample_id").set_index("sample_id")
        if "layer" in node:
            return ids.map(manifest[LAYER_COLUMNS[node["layer"]]]).eq(True).astype(float)
        return ids.map(manifest["sample_type"]).isin(list(node["sample_type"])).astype(float)
    if "gene" in node:
        mutation = sources["mutation"]
        calls = pd.to_numeric(mutation.loc[node["gene"]], errors="coerce") if node["gene"] in mutation.index \
            else pd.Series(dtype=float)
        value = ids.map(calls)
        hit = (value > 0) if node.get("mutated", True) else (value == 0)
        return hit.astype(float).where(value.notna())
    if "slide_kinds" in node:
        with_slide = set(slides_of_kind(sources["slides"], node["slide_kinds"])["sample_id"])
        return ids.isin(with_slide).astype(float)
    table = sources[node["table"]].drop_duplicates("sample_id").set_index("sample_id")
    values = ids.map(table[node["column"]])
    kept = filter_column(pd.DataFrame({"v": values.values}), "v", node["cmp"], node.get("value")).index
    if node["cmp"] == "notnull":                # 'is recorded' is itself a question about missing values
        unknown = pd.Series(False, index=ids.index)
    elif node["cmp"] == "in_raw":               # exact raw values: only a truly empty value is unknown
        unknown = values.map(lambda x: x is None or (isinstance(x, float) and pd.isna(x))
                             or str(x).strip() == "" or str(x).strip().startswith("["))
    else:
        unknown = values.map(is_missing)
    out = pd.Series(0.0, index=ids.index)
    out[unknown.astype(bool).to_numpy()] = float("nan")
    out.iloc[list(kept)] = 1.0
    return out


def filter_where(df, node, sources, apply_to="sample"):
    """Keep rows whose sample meets the condition (true, not unknown).

    apply_to: "sample" (the row's own sample), "added_normals" (the normal sample a tumour-normal pairs join
    added to the row) or "both" (the row's sample and its paired normal must each meet it).
    """
    columns = {"sample": ["sample_id"], "added_normals": ["normal.sample_id"],
               "both": ["sample_id", "normal.sample_id"]}[apply_to]
    keep = pd.Series(True, index=df.index)
    for col in columns:
        keep &= pd.Series(evaluate_condition(node, df[col], sources).eq(1.0).to_numpy(), index=df.index)
    return df[keep].copy()


def cohort_sample_ids(df, extra_columns=()):
    ids = set(df["sample_id"].dropna())
    for col in extra_columns:
        if col in df.columns:
            ids |= set(df[col].dropna())
    return sorted(ids)
