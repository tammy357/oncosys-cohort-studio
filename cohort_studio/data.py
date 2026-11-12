"""Load the frozen TCGA-BRCA release and describe which fields exist in it."""
import csv
import hashlib
import pathlib

import pandas as pd

from . import ops

DEFAULT_DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data" / "tcga-brca"

# Friendly field names -> candidate column names. The first column that exists is used,
# and the tool shows which one it picked, so nothing is matched silently.
CLINICAL_FIELDS = {
    "er_status": {"label": "ER status (clinical IHC)", "columns": ["breast_carcinoma_estrogen_receptor_status", "ER_Status_nature2012"]},
    "pr_status": {"label": "PR status (clinical IHC)", "columns": ["breast_carcinoma_progesterone_receptor_status", "PR_Status_nature2012"]},
    "her2_status": {"label": "HER2 status (clinical)", "columns": ["lab_proc_her2_neu_immunohistochemistry_receptor_status", "HER2_Final_Status_nature2012"]},
    "pam50": {"label": "PAM50 subtype (RNA call)", "columns": ["PAM50Call_RNAseq", "PAM50_mRNA_nature2012"]},
    "stage": {"label": "Pathologic stage", "columns": ["pathologic_stage", "AJCC_Stage_nature2012"]},
    "age": {"label": "Age at diagnosis", "columns": ["age_at_initial_pathologic_diagnosis", "Age_at_Initial_Pathologic_Diagnosis_nature2012"]},
    "histology": {"label": "Histological type", "columns": ["histological_type"]},
    "gender": {"label": "Recorded gender", "columns": ["gender", "Gender_nature2012"]},
    "menopause": {"label": "Menopause status", "columns": ["menopause_status"]},
}
SURVIVAL_FIELDS = {
    "os": {"label": "Overall survival event", "columns": ["OS"]},
    "os_time": {"label": "Overall survival time (days)", "columns": ["OS.time"]},
    "pfi": {"label": "Progression-free interval event", "columns": ["PFI"]},
    "pfi_time": {"label": "Progression-free interval time (days)", "columns": ["PFI.time"]},
    "dss": {"label": "Disease-specific survival event", "columns": ["DSS"]},
    "dfi": {"label": "Disease-free interval event", "columns": ["DFI"]},
    "redaction": {"label": "Redaction flag (TCGA-CDR)", "columns": ["Redaction"]},
}
TABLE_FIELDS = {"clinical": CLINICAL_FIELDS, "survival": SURVIVAL_FIELDS}

RAW_FILES = {
    "clinical": "clinical.tsv",
    "survival": "survival.tsv",
    "mutation": "mutation_gene_level.tsv.gz",
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class DataStore:
    """The frozen release: manifests always, raw files when fetch.py has downloaded them."""

    def __init__(self, data_dir=None):
        self.data_dir = pathlib.Path(data_dir or DEFAULT_DATA_DIR)
        self.manifest = ops.load_manifest(self.data_dir)
        self.slides = ops.load_slides(self.data_dir)
        self.file_status = self._check_files()
        self._tables = {}

    # ---------- files and checksums ----------
    def _check_files(self):
        expected = {}
        sources = self.data_dir / "sources.csv"
        if sources.exists():
            with open(sources) as f:
                expected = {row["file"]: row["sha256"] for row in csv.DictReader(f)}
        status = {}
        for name in ["sample_manifest.csv", "slide_manifest.csv"]:
            path = self.data_dir / name
            status[name] = {"present": path.exists(), "verified": None, "sha256": sha256(path) if path.exists() else None,
                            "note": "committed manifest"}
        for table, name in RAW_FILES.items():
            path = self.data_dir / "raw" / name
            entry = {"present": path.exists(), "verified": None, "sha256": None, "note": ""}
            if path.exists():
                entry["sha256"] = sha256(path)
                if name in expected:
                    entry["verified"] = entry["sha256"] == expected[name]
                    entry["note"] = "matches the frozen checksum" if entry["verified"] else \
                        "DOES NOT match the frozen checksum: tell the organizers and don't use it for judged results"
            else:
                entry["note"] = "not downloaded yet: run python data/tcga-brca/fetch.py"
            status[name] = entry
        return status

    def available(self, table):
        if table in ("manifest", "slides", "normals"):
            return True
        entry = self.file_status.get(RAW_FILES.get(table, ""), {})
        return bool(entry.get("present")) and entry.get("verified") is not False

    def table(self, name):
        if name not in self._tables:
            if name == "clinical":
                self._tables[name] = ops.load_clinical(self.data_dir)
            elif name == "survival":
                self._tables[name] = ops.load_survival(self.data_dir)
            elif name == "mutation":
                self._tables[name] = ops.load_mutation(self.data_dir)
            else:
                raise KeyError(name)
        return self._tables[name]

    # ---------- fields ----------
    def resolve_field(self, table, field):
        """Return the real column name for a friendly field, or None if this release lacks it."""
        catalogue = TABLE_FIELDS.get(table, {})
        if field in catalogue:
            candidates = catalogue[field]["columns"]
        else:
            candidates = [field]  # allow a raw column name typed by the user
        if not self.available(table):
            return None
        cols = self.table(table).columns
        for c in candidates:
            if c in cols:
                return c
        return None

    def field_label(self, table, field):
        return TABLE_FIELDS.get(table, {}).get(field, {}).get("label", field)

    def field_values(self, table, field, limit=12):
        col = self.resolve_field(table, field)
        if col is None:
            return {}
        return self.table(table)[col].fillna("(blank)").value_counts().head(limit).to_dict()

    def genes(self):
        if not self.available("mutation"):
            return set()
        return set(self.table("mutation").index.astype(str))

    # ---------- composition (who is in the cohort) ----------
    def composition_frame(self):
        """One row per sample with the characteristics used to describe who a step removed."""
        if "_composition" in self._tables:
            return self._tables["_composition"]
        m = self.manifest[["sample_id", "patient_id", "sample_type"]].copy()
        m["Submitting site"] = m["sample_id"].str[5:7]
        m = m.rename(columns={"sample_type": "Sample type"})
        if self.available("clinical"):
            clin = self.table("clinical").drop_duplicates("sample_id").set_index("sample_id")
            def take(field, transform=None):
                col = self.resolve_field("clinical", field)
                if col is None:
                    return None
                values = m["sample_id"].map(clin[col])
                return values.map(transform) if transform else values.map(lambda v: None if ops.is_missing(v) else v)
            pam = take("pam50")
            if pam is not None:
                m["PAM50 subtype"] = pam
            er = take("er_status")
            if er is not None:
                m["ER status (IHC)"] = er
            stage = take("stage", ops.stage_group)
            if stage is not None:
                m["Stage"] = stage
            age = take("age")
            if age is not None:
                num = pd.to_numeric(age, errors="coerce")
                m["Age band"] = pd.cut(num, [0, 49, 64, 200], labels=["under 50", "50-64", "65 and over"]).astype(object)
        for col in m.columns:
            if col not in ("sample_id", "patient_id"):
                m[col] = m[col].where(m[col].notna(), "(not recorded)").astype(str)
        m = m.set_index("sample_id")
        self._tables["_composition"] = m
        return m


# Which composition column a filter on each field changes on purpose (so it isn't reported as a bias).
FIELD_TO_COMPOSITION = {
    ("clinical", "pam50"): "PAM50 subtype",
    ("clinical", "er_status"): "ER status (IHC)",
    ("clinical", "stage"): "Stage",
    ("clinical", "age"): "Age band",
}
