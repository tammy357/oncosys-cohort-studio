"""Rebuild the compact case-by-protein table from frozen BCM inputs."""
import csv
import gzip
import hashlib
import shutil
from pathlib import Path
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
GENES = {"ENSG00000146648":"EGFR", "ENSG00000141736":"ERBB2",
         "ENSG00000133703":"KRAS", "ENSG00000141510":"TP53",
         "ENSG00000171862":"PTEN", "ENSG00000111640":"GAPDH"}

def build(raw, output):
    rows = []
    for src in csv.DictReader((HERE / "protein_sources.csv").open()):
        with gzip.open(raw / src["file"], "rt", newline="") as f:
            r = csv.reader(f, delimiter="\t")
            header = next(r)[1:]
            assert len(header) == len(set(header))
            values = {}
            for row in r:
                gene = row[0].split(".")[0]
                if gene in GENES:
                    assert gene not in values and len(row) == len(header) + 1
                    values[gene] = row[1:]
            for i, case in enumerate(header):
                row = []
                for gene in GENES:
                    value = values.get(gene, [""] * len(header))[i].strip()
                    row.append(f"{float(value):.4f}" if value.lower() not in {"", "na", "nan", "null"} else "")
                rows.append([src["cohort"], case, *row])
    with output.open("w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["cohort", "case_id", *GENES.values()])
        w.writerows(sorted(rows))

def main():
    raw = HERE / "raw"
    raw.mkdir(exist_ok=True)
    for src in csv.DictReader((HERE / "protein_sources.csv").open()):
        path = raw / src["file"]
        if not path.exists():
            temp = path.with_suffix(".part")
            with urlopen(src["url"], timeout=45) as response, temp.open("wb") as f:
                shutil.copyfileobj(response, f)
            temp.replace(path)
        with path.open("rb") as f:
            if hashlib.file_digest(f, "sha256").hexdigest() != src["sha256"]:
                raise SystemExit(f"Checksum mismatch: {path}")
    build(raw, HERE / "protein_starter.csv")

if __name__ == "__main__":
    main()
