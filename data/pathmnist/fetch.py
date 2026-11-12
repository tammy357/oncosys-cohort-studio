"""Download PathMNIST into data/pathmnist/raw/ and verify it against the publisher's MD5 checksum.

Usage (from the repo root):
  python data/pathmnist/fetch.py            # 28 x 28 pixels (default; the smallest download)
  python data/pathmnist/fetch.py --size 64  # sharper; recommended for image-quality work
  python data/pathmnist/fetch.py --size 224 # largest; optional, for image-quality work
Checksums come from the official medmnist package (see sources.csv).
"""
import csv, hashlib, pathlib, sys, urllib.request

HERE = pathlib.Path(__file__).resolve().parent
RAW = HERE / "raw"

def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def main():
    size = sys.argv[sys.argv.index("--size") + 1] if "--size" in sys.argv else "28"
    row = next((r for r in csv.DictReader(open(HERE / "sources.csv")) if r["size_px"] == size), None)
    if row is None:
        sys.exit("Choose --size 28, 64, 128 or 224")
    RAW.mkdir(exist_ok=True)
    dest = RAW / row["file"]
    if not dest.exists() or md5(dest) != row["md5"]:
        print(f"fetch  {row['file']} from Zenodo (this can take a few minutes)")
        urllib.request.urlretrieve(row["source_url"], dest)
    if md5(dest) != row["md5"]:
        sys.exit(f"CHECKSUM MISMATCH for {row['file']}: delete it and try again, or tell the organizers")
    print(f"ok     {dest}")

if __name__ == "__main__":
    main()
