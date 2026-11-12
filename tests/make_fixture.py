"""Build a tiny SYNTHETIC release with the same file layout as data/tcga-brca, for tests.

Every barcode uses the made-up site code 'ZZ' and every value is invented. It mimics the shape of
the real files (sample-level rows, a patient with tumour + normal, several slides per sample) so
the tests can exercise each kind of join without the real data.
"""
import gzip
import hashlib
import pathlib

import pandas as pd


def _sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def build(root, collection_days=False):
    """collection_days=True adds a made-up days_to_collection column (the real release may not have one)."""
    root = pathlib.Path(root)
    (root / "raw").mkdir(parents=True, exist_ok=True)
    samples = []
    # 8 patients with a primary tumour; patients 1-3 also have a normal sample; patient 8 has a metastasis too
    for i in range(1, 9):
        pid = f"TCGA-ZZ-{i:04d}"
        samples.append({"sample_id": f"{pid}-01", "patient_id": pid, "sample_type": "Primary Tumor",
                        "in_clinical": True, "has_survival": True, "has_mutation": i != 7, "has_copy_number": True,
                        "has_rna": True, "has_protein": i <= 5, "slides_for_sample": 0})
        if i <= 3:
            samples.append({"sample_id": f"{pid}-11", "patient_id": pid, "sample_type": "Solid Tissue Normal",
                            "in_clinical": True, "has_survival": True, "has_mutation": False, "has_copy_number": False,
                            "has_rna": True, "has_protein": False, "slides_for_sample": 0})
        if i == 8:
            samples.append({"sample_id": f"{pid}-06", "patient_id": pid, "sample_type": "Metastatic",
                            "in_clinical": True, "has_survival": True, "has_mutation": False, "has_copy_number": True,
                            "has_rna": True, "has_protein": False, "slides_for_sample": 0})
    slides = []
    plan = {1: ["DX1", "DX2", "TS1"], 2: ["DX1", "TS1", "BS1"], 3: ["TS1"], 4: ["DX1"], 5: ["DX1", "TS1"],
            6: ["DX1", "DX2"], 7: ["DX1"], 8: []}
    kind = {"DX": "diagnostic (FFPE)", "TS": "frozen, top section", "BS": "frozen, bottom section", "MS": "frozen, middle section"}
    for i, codes in plan.items():
        pid = f"TCGA-ZZ-{i:04d}"
        for c in codes:
            slides.append({"slide_barcode": f"{pid}-01Z-00-{c}", "sample_id": f"{pid}-01", "patient_id": pid,
                           "slide_kind": kind[c[:2]], "objective": 40, "size_mb": 1.0, "license": "synthetic",
                           "viewer_url": "", "study_uid": "", "series_uid": ""})
    m = pd.DataFrame(samples)
    m["slides_for_sample"] = m["sample_id"].map(pd.DataFrame(slides).groupby("sample_id").size()).fillna(0).astype(int)
    m.to_csv(root / "sample_manifest.csv", index=False)
    pd.DataFrame(slides).to_csv(root / "slide_manifest.csv", index=False)

    er = {1: "Positive", 2: "Positive", 3: "Negative", 4: "Positive", 5: "Negative", 6: "[Not Evaluated]", 7: "Positive", 8: "Negative"}
    pam = {1: "LumA", 2: "LumB", 3: "Basal", 4: "LumA", 5: "Basal", 6: "", 7: "LumA", 8: "Her2"}
    stage = {1: "Stage IIA", 2: "Stage I", 3: "Stage IIIB", 4: "Stage IIB", 5: "Stage IV", 6: "[Not Available]", 7: "Stage IA", 8: "Stage IIIA"}
    age = {1: 45, 2: 62, 3: 51, 4: 70, 5: 39, 6: 58, 7: 66, 8: 49}
    clin, surv = [], []
    for s in samples:
        i = int(s["patient_id"][-4:])
        clin.append({"sampleID": s["sample_id"], "_PATIENT": s["patient_id"],
                     "breast_carcinoma_estrogen_receptor_status": er[i],
                     "breast_carcinoma_progesterone_receptor_status": "Negative" if i in (3, 5) else "Positive",
                     "lab_proc_her2_neu_immunohistochemistry_receptor_status": "Positive" if i == 8 else "Negative",
                     "PAM50Call_RNAseq": pam[i] if s["sample_type"] == "Primary Tumor" else "",
                     "pathologic_stage": stage[i], "age_at_initial_pathologic_diagnosis": age[i],
                     "histological_type": "Infiltrating Lobular Carcinoma" if i == 4 else "Infiltrating Ductal Carcinoma",
                     "gender": "FEMALE", "menopause_status": "Post (prior bilateral ovariectomy)" if age[i] > 50 else "Pre (<6 months since LMP)"})
        if collection_days:
            # patient 8: the metastasis (-06) was collected before the primary tumour; normals have no day
            day = {"01": 30 + i, "06": 10, "11": ""}[s["sample_id"][-2:]]
            clin[-1]["days_to_collection"] = day
        surv.append({"sample": s["sample_id"], "_PATIENT": s["patient_id"], "OS": int(i in (3, 5)),
                     "OS.time": 400 + 50 * i, "PFI": "" if i == 6 else int(i in (3, 5, 8)), "PFI.time": 300 + 40 * i})
    pd.DataFrame(clin).to_csv(root / "raw" / "clinical.tsv", sep="\t", index=False)
    pd.DataFrame(surv).to_csv(root / "raw" / "survival.tsv", sep="\t", index=False)
    mut_samples = [s["sample_id"] for s in samples if s["has_mutation"]]
    mut = pd.DataFrame(0, index=["TP53", "PIK3CA", "GATA3"], columns=mut_samples)
    for sid in mut_samples:
        i = int(sid[8:12])
        mut.loc["TP53", sid] = int(i in (3, 5, 8))
        mut.loc["PIK3CA", sid] = int(i in (1, 4))
    mut.index.name = "sample"
    with gzip.open(root / "raw" / "mutation_gene_level.tsv.gz", "wt") as f:
        mut.to_csv(f, sep="\t")
    rows = [{"file": n, "layer": "synthetic", "source_url": "", "sha256": _sha(root / "raw" / n), "bytes": 0,
             "license": "synthetic", "retrieved": ""} for n in ["clinical.tsv", "survival.tsv", "mutation_gene_level.tsv.gz"]]
    pd.DataFrame(rows).to_csv(root / "sources.csv", index=False)
    return root


if __name__ == "__main__":
    import sys
    print(build(sys.argv[1] if len(sys.argv) > 1 else "tests/fixture_release"))
