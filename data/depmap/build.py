"""Build the compact challenge pack from the original DepMap 24Q4 files."""
import csv
import hashlib
from pathlib import Path
import shutil
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"
GENES = ("KRAS", "NRAS", "BRAF", "EGFR", "PIK3CA", "TP53", "STK11", "KEAP1")
TEST = {"Lung", "Bowel"}

def open_matrix(path, genes):
    with path.open(newline="") as f:
        rows = csv.reader(f)
        header = next(rows)
        idx = [next(i for i, h in enumerate(header) if h.split(" (")[0] == g) for g in genes]
        return {row[0]: [row[i] for i in idx] for row in rows if row and row[0]}

def build(raw, output):
    expression = open_matrix(raw / "OmicsExpressionProteinCodingGenesTPMLogp1.csv", GENES)
    effects = open_matrix(raw / "CRISPRGeneEffect.csv", ("KRAS",))
    with (raw / "Model.csv").open(newline="") as f:
        models = {r["ModelID"]: r for r in csv.DictReader(f)}
    records = []
    test_donors = {m["PatientID"] for m in models.values() if m["OncotreeLineage"] in TEST and m["PatientID"]}
    for model_id in sorted(expression.keys() & effects.keys() & models.keys()):
        lineage = models[model_id]["OncotreeLineage"]
        if not lineage or not all(expression[model_id]) or not effects[model_id][0]:
            continue
        donor = models[model_id]["PatientID"]
        if not donor or (lineage not in TEST and donor in test_donors):
            continue
        values = [*expression[model_id], effects[model_id][0]]
        records.append([model_id, lineage, "test" if lineage in TEST else "train",
                        *(f"{float(v):.4f}" for v in values)])
    assert {r[2] for r in records} == {"train", "test"}
    with output.open("w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["model_id", "lineage", "split", *GENES, "kras_gene_effect"])
        w.writerows(records)

def main():
    RAW.mkdir(exist_ok=True)
    for source in csv.DictReader((HERE / "sources.csv").open()):
        path = RAW / source["file"]
        if not path.exists():
            temp = path.with_suffix(".part")
            with urlopen(source["url"], timeout=60) as r, temp.open("wb") as f:
                shutil.copyfileobj(r, f)
            temp.replace(path)
        with path.open("rb") as f:
            actual = hashlib.file_digest(f, "sha256").hexdigest()
        if actual != source["sha256"]:
            raise SystemExit(f"Checksum mismatch: {path}; remove and retry")
    build(RAW, HERE / "starter.csv")

if __name__ == "__main__":
    main()
