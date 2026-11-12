"""Two reference analyses that tools can rerun on any release of the TCGA-BRCA files.

  A1 · ER-alpha protein by clinical ER status   (uses clinical + protein)
  A2 · RNA subtype mix of primary tumors        (uses clinical only)

Usage: python data/tcga-brca/reference_analyses.py [release_dir]   (default: data/tcga-brca/raw)
Prints a JSON summary so results from two releases can be compared.
"""
import json, pathlib, sys
import pandas as pd

def load(release):
    r = pathlib.Path(release)
    clinical = pd.read_csv(r / "clinical.tsv", sep="\t", low_memory=False)
    protein = pd.read_csv(r / "protein_rppa.tsv.gz", sep="\t", index_col=0)
    return clinical, protein

def primary(ids):
    return [i for i in ids if str(i)[13:15] == "01"]

def a1(clinical, protein):
    """Mean ER-alpha (RPPA) in ER-positive vs ER-negative primary tumors (clinical IHC status)."""
    er = clinical.set_index("sampleID")["breast_carcinoma_estrogen_receptor_status"]
    samples = [s for s in primary(protein.columns) if er.get(s) in ("Positive", "Negative")]
    vals = protein.loc["ERALPHA", samples]
    groups = {k: vals[[s for s in samples if er[s] == k]] for k in ("Positive", "Negative")}
    return {"analysis": "A1", "inputs": ["clinical.tsv", "protein_rppa.tsv.gz"],
            "n_samples": len(samples), "sample_ids": sorted(samples),
            "n_positive": int(groups["Positive"].size), "n_negative": int(groups["Negative"].size),
            "mean_positive": round(float(groups["Positive"].mean()), 3),
            "mean_negative": round(float(groups["Negative"].mean()), 3),
            "difference": round(float(groups["Positive"].mean() - groups["Negative"].mean()), 3)}

def a2(clinical):
    """Share of primary tumors in each PAM50 RNA-seq subtype (subtyped samples only)."""
    c = clinical[clinical.sampleID.str[13:15] == "01"].dropna(subset=["PAM50Call_RNAseq"])
    counts = c.PAM50Call_RNAseq.value_counts().sort_index()
    return {"analysis": "A2", "inputs": ["clinical.tsv"], "n_samples": int(counts.sum()),
            "sample_ids": sorted(c.sampleID), "counts": {k: int(v) for k, v in counts.items()},
            "percent": {k: round(100 * v / counts.sum(), 1) for k, v in counts.items()}}

if __name__ == "__main__":
    release = sys.argv[1] if len(sys.argv) > 1 else "data/tcga-brca/raw"
    clinical, protein = load(release)
    out = [a1(clinical, protein), a2(clinical)]
    for o in out:
        o["n_ids_listed"] = len(o.pop("sample_ids"))
    print(json.dumps(out, indent=2))
