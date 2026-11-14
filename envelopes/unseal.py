"""Open a sealed envelope with its unlock code.

On Saturday, at the start of your Q&A, a judge gives you an envelope number and a four-word code.
Run, from the repo root:

  python envelopes/unseal.py 07 amber falcon river quartz

What happens:
  1. Checks your release A files (from data/tcga-brca/fetch.py) against the published fingerprints.
  2. Checks the envelope file against envelopes/SHA256SUMS, so you know it wasn't altered.
  3. Opens the envelope with the code. A wrong code opens nothing.
  4. Writes everything to envelopes/opened/<envelope>/ and checks every file's fingerprint:
       release/                 a new TCGA-BRCA release (5 files: no RNA), same file names and format as release A
       ...plus the other judge inputs (repair desk file, research brief, cohort request, questions)

Practice with the public practice envelope (code in envelopes/README.md):
  python envelopes/unseal.py practice <practice code>

Needs: pandas and cryptography (both in requirements.txt; both preinstalled in Colab).
"""
import base64, gzip, hashlib, io, json, pathlib, sys, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
HERE = ROOT / "envelopes"
RAW = ROOT / "data" / "tcga-brca" / "raw"
MAGIC = b"ONCOSEAL1"
RELEASE_FILES = ["clinical.tsv", "survival.tsv", "mutation_gene_level.tsv.gz",
                 "copy_number_gistic.tsv.gz", "protein_rppa.tsv.gz"]  # RNA is not part of envelope releases


def say(mark, text):
    print(f"  {mark} {text}", flush=True)


def stop(text):
    print(f"\n  ✗ {text}\n", flush=True)
    sys.exit(1)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def content_hash(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def normalise_code(words):
    return "-".join(w.strip().lower() for w in " ".join(words).replace("-", " ").split())


def envelope_name(arg):
    arg = arg.strip()
    if arg.lower() == "practice":
        return "practice"
    digits = arg.upper().lstrip("E")
    if not digits.isdigit():
        stop(f"'{arg}' isn't an envelope number. Use the number on your card, for example 07.")
    return f"E{int(digits):02d}"


def check_release_a():
    import csv
    if not RAW.exists():
        stop("Release A isn't here yet. Run  python data/tcga-brca/fetch.py  first, then try again.")
    expected = {r["file"]: r["sha256"] for r in csv.DictReader(open(ROOT / "data" / "tcga-brca" / "sources.csv"))}
    for name in RELEASE_FILES:
        p = RAW / name
        if not p.exists() or sha256_file(p) != expected.get(name):
            stop(f"{name} in data/tcga-brca/raw/ is missing or doesn't match the published fingerprint. "
                 "Run  python data/tcga-brca/fetch.py  again, then retry.")
    say("✓", "Release A matches the published fingerprints")


def check_envelope(path):
    sums = {}
    for line in open(HERE / "SHA256SUMS"):
        if line.strip():
            digest, name = line.split()
            sums[name] = digest
    if path.name not in sums:
        stop(f"{path.name} isn't listed in envelopes/SHA256SUMS. Pull the latest repo (git pull) and retry.")
    if sha256_file(path) != sums[path.name]:
        stop(f"{path.name} doesn't match its published fingerprint. Pull the latest repo (git pull) and retry.")
    say("✓", f"Envelope {path.stem} matches its published fingerprint")


def open_envelope(path, code):
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    blob = path.read_bytes()
    if not blob.startswith(MAGIC):
        stop(f"{path.name} isn't a sealed envelope.")
    salt, nonce, sealed = blob[9:25], blob[25:37], blob[37:]
    key = Scrypt(salt=salt, length=32, n=2 ** 17, r=8, p=1).derive(code.encode())
    try:
        packed = AESGCM(key).decrypt(nonce, sealed, MAGIC)
    except InvalidTag:
        stop(f"That code doesn't open envelope {path.stem}. Check the envelope number and the four words.")
    say("✓", f"Envelope {path.stem} opened")
    return json.loads(gzip.decompress(packed))


def read_raw(name):
    import pandas as pd
    if name == "clinical.tsv":
        return pd.read_csv(RAW / name, sep="\t", dtype=str, keep_default_na=False)
    return pd.read_csv(RAW / name, sep="\t", index_col=0, dtype=str, keep_default_na=False)


def write(df, dest, name):
    if name == "clinical.tsv":
        df.to_csv(dest / name, sep="\t", index=False, lineterminator="\n")
    else:
        df.to_csv(dest / name, sep="\t", lineterminator="\n", compression={"method": "gzip", "mtime": 0} if name.endswith(".gz") else None)


def rebuild(payload, dest):
    import shutil
    dest.mkdir(parents=True)
    for name in RELEASE_FILES:
        patch = payload["files"].get(name)
        if patch is None:
            shutil.copy(RAW / name, dest / name)
            continue
        df = read_raw(name)
        if name == "clinical.tsv":
            for sid, cells in patch["rows"].items():
                for col, val in cells.items():
                    df.loc[df.sampleID == sid, col] = val
        else:
            for col, vals in patch["columns"].items():
                df[col] = vals
            df = df[patch["order"]]
        write(df, dest, name)
    bad = [n for n in RELEASE_FILES if content_hash(dest / n) != payload["content_sha256"][n]]
    if bad:
        stop(f"Rebuilt files don't match the expected fingerprints: {bad}. "
             "Tell a judge right away; they have a backup.")
    say("✓", f"All {len(RELEASE_FILES)} release files match their fingerprints")


def safe_output():
    # Windows consoles and captured output often use cp1252, which can't print ✓ or ✗.
    # Replace what can't be encoded instead of crashing.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def main():
    safe_output()
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    started = time.time()
    name = envelope_name(sys.argv[1])
    code = normalise_code(sys.argv[2:])
    path = HERE / "sealed" / f"{name}.sealed"
    print(f"\nOpening envelope {name}\n", flush=True)
    if not path.exists():
        stop(f"There's no envelope {name}. Pull the latest repo (git pull), and check the number on your card.")
    check_release_a()
    check_envelope(path)
    payload = open_envelope(path, code)
    out = HERE / "opened" / name
    if out.exists():
        import shutil
        shutil.rmtree(out)
    rebuild(payload, out / "release")
    for fname, text in payload.get("extras", {}).items():
        (out / fname).write_text(text, encoding="utf-8")
    elapsed = time.time() - started
    print(f"\n  Done in {elapsed:.1f} s. Everything is in:  {out.relative_to(ROOT)}/")
    print(f"  New release for your tool:   {(out / 'release').relative_to(ROOT)}/")
    for fname in payload.get("extras", {}):
        print(f"  Also inside:                 {fname}")
    print()


if __name__ == "__main__":
    main()
