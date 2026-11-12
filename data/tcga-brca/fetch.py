"""Download the TCGA-BRCA files listed in sources.csv into data/tcga-brca/raw/ and verify each checksum.

Usage (from the repo root):  python data/tcga-brca/fetch.py            # everything (~70 MB)
                             python data/tcga-brca/fetch.py --no-rna   # skip the 64 MB RNA file
Files are fetched from the public UCSC Xena hub, not stored in this repository.
"""
import csv, hashlib, pathlib, sys, urllib.request

HERE = pathlib.Path(__file__).resolve().parent
RAW = HERE / "raw"

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def main():
    RAW.mkdir(exist_ok=True)
    skip_rna = "--no-rna" in sys.argv
    problems = 0
    for row in csv.DictReader(open(HERE / "sources.csv")):
        if skip_rna and row["file"].startswith("rna_"):
            print(f"skip   {row['file']}")
            continue
        dest = RAW / row["file"]
        if not dest.exists() or sha256(dest) != row["sha256"]:
            print(f"fetch  {row['file']} ({int(row['bytes']) / 1e6:.1f} MB)")
            urllib.request.urlretrieve(row["source_url"], dest)
        if sha256(dest) == row["sha256"]:
            print(f"ok     {row['file']}")
        else:
            problems += 1
            print(f"CHANGED {row['file']}: the source no longer matches the event's frozen version. "
                  "Tell the organizers; don't use it for judged results.")
    sys.exit(1 if problems else 0)

if __name__ == "__main__":
    main()
