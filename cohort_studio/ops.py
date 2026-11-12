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


def cohort_sample_ids(df, extra_columns=()):
    ids = set(df["sample_id"].dropna())
    for col in extra_columns:
        if col in df.columns:
            ids |= set(df[col].dropna())
    return sorted(ids)
