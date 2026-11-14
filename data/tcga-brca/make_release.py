"""Build a changed "release" of the TCGA-BRCA files, for testing tools against "The Result Changed".

Usage (from the repo root, after fetch.py):
  python data/tcga-brca/make_release.py                      # builds the public practice release B
  python data/tcga-brca/make_release.py --verify             # checks your practice-B against the published content hashes
  python data/tcga-brca/make_release.py --config my.json     # organizers: a private release from a config file

Values are copied as their original text, so a release is the same on any machine. Hashes cover each file's
decompressed content (see practice-B.content.sha256), because gzip bytes can differ between systems.

Output: data/tcga-brca/releases/<name>/ with the same file names as raw/, plus CHANGES.json listing exactly
what was altered. CHANGES.json is the answer key for practice: don't read it until your tool has run.
Releases are generated locally and never committed.
"""
import gzip, hashlib, json, pathlib, random, shutil, sys
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
RAW, OUT = HERE / "raw", HERE / "releases"

PRACTICE = {"name": "practice-B", "seed": 2026,
            "changes": [{"type": "swap_samples", "file": "protein_rppa.tsv.gz", "count": 1, "opposite_er": True},
                        {"type": "replace_with_duplicates", "file": "protein_rppa.tsv.gz", "count": 5, "subtype": "Her2"}]}

def read(name):
    # dtype=str + keep_default_na=False: every cell keeps its exact original text
    if name == "clinical.tsv":
        return pd.read_csv(RAW / name, sep="\t", dtype=str, keep_default_na=False)
    return pd.read_csv(RAW / name, sep="\t", index_col=0, dtype=str, keep_default_na=False)

def content_hash(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()

def verify(name="practice-B"):
    expected = dict(line.split()[::-1] for line in open(HERE / f"{name}.content.sha256") if line.strip())
    bad = [f for f, h in expected.items() if content_hash(OUT / name / f) != h]
    print("practice-B matches the published release" if not bad else f"MISMATCH in {bad}: rebuild from a fresh fetch")
    sys.exit(1 if bad else 0)

def write(df, dest, name):
    if name == "clinical.tsv":
        df.to_csv(dest / name, sep="\t", index=False, lineterminator="\n")
    else:
        df.to_csv(dest / name, sep="\t", lineterminator="\n", compression={"method": "gzip", "mtime": 0} if name.endswith(".gz") else None)

def main():
    if "--verify" in sys.argv:
        verify()
    cfg = PRACTICE
    if "--config" in sys.argv:
        cfg = json.load(open(sys.argv[sys.argv.index("--config") + 1]))
    rng = random.Random(cfg["seed"])
    clinical = read("clinical.tsv")
    er = clinical.set_index("sampleID")["breast_carcinoma_estrogen_receptor_status"]
    pam50 = clinical.set_index("sampleID")["PAM50Call_RNAseq"]
    dest = OUT / cfg["name"]
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    frames, log = {}, []
    for ch in cfg["changes"]:
        name = ch["file"]
        df = frames.get(name)
        if df is None:
            df = read(name)
        if ch["type"] == "swap_samples":                      # two samples' measurements trade labels
            cols = [c for c in df.columns if c[13:15] == "01" and er.get(c) in ("Positive", "Negative")]
            for _ in range(ch["count"]):
                a = rng.choice([c for c in cols if er[c] == "Positive"])
                b = rng.choice([c for c in cols if er[c] == ("Negative" if ch.get("opposite_er") else "Positive") and c != a])
                df = df.rename(columns={a: b, b: a})[df.columns]
                log.append({"type": "swap_samples", "file": name, "samples": [a, b]})
        elif ch["type"] == "replace_with_duplicates":         # same count; some samples replaced by copies under other IDs
            pool = [c for c in df.columns if c[13:15] == "01"]
            victims = rng.sample(sorted(c for c in pool if pam50.get(c) == ch["subtype"]), ch["count"])
            donors = rng.sample(sorted(c for c in pool if pam50.get(c) == "LumA"), ch["count"])
            unused = sorted(s for s in clinical.sampleID if s[13:15] == "01" and s not in df.columns)
            new_ids = rng.sample(unused, ch["count"])
            df = df.drop(columns=victims)
            copies = pd.DataFrame({n: df[d] for d, n in zip(donors, new_ids)}, index=df.index)
            df = pd.concat([df, copies], axis=1)
            log.append({"type": "replace_with_duplicates", "file": name, "removed": victims,
                        "duplicates": [{"new_id": n, "copied_from": d} for d, n in zip(donors, new_ids)],
                        "note": f"{ch['subtype']} loses {ch['count']} protein samples; total sample count unchanged"})
        elif ch["type"] == "relabel_clinical":                # a clinical field changes for some samples
            col = clinical[ch["column"]]
            eligible = clinical[(clinical.sampleID.str[13:15] == "01") & (col != "") & (col != ch["new_value"])]
            pick = rng.sample(sorted(eligible.sampleID), ch["count"])
            before = {s: clinical.loc[clinical.sampleID == s, ch["column"]].item() for s in pick}
            clinical.loc[clinical.sampleID.isin(pick), ch["column"]] = ch["new_value"]
            df = clinical
            log.append({"type": "relabel_clinical", "file": name, "column": ch["column"], "before": before, "new_value": ch["new_value"]})
        else:
            sys.exit(f"unknown change type: {ch['type']}")
        frames[name] = df
    for f in RAW.iterdir():
        if f.name in frames:
            write(frames[f.name], dest, f.name)
        else:
            shutil.copy(f, dest / f.name)
    json.dump({"release": cfg["name"], "based_on": "raw (checksums in sources.csv)", "changes": log},
              open(dest / "CHANGES.json", "w"), indent=2)
    print(f"built {dest} with {len(log)} change(s); answer key in CHANGES.json")

if __name__ == "__main__":
    main()
