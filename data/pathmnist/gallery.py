"""Build a browsable gallery of labelled PathMNIST patches: data/pathmnist/gallery/index.html.

Usage (from the repo root, after fetch.py):  python data/pathmnist/gallery.py [--size 28|64|128|224] [--per-class 24]
"""
import html, pathlib, sys
import numpy as np
from PIL import Image

HERE = pathlib.Path(__file__).resolve().parent
LABELS = ["adipose", "background", "debris", "lymphocytes", "mucus", "smooth muscle",
          "normal colon mucosa", "cancer-associated stroma", "colorectal adenocarcinoma epithelium"]
GUIDE = ["Fat tissue: large, empty-looking cells", "No tissue: glass or empty slide",
         "Dead cells and fragments", "Immune cells: small, dark, round nuclei",
         "Mucus: pale, wispy material", "Muscle: long pink fibers",
         "Healthy lining of the colon", "Supporting tissue around a tumor",
         "Tumor cells from the colon lining"]

def main():
    size = sys.argv[sys.argv.index("--size") + 1] if "--size" in sys.argv else "28"
    per = int(sys.argv[sys.argv.index("--per-class") + 1]) if "--per-class" in sys.argv else 24
    npz = np.load(HERE / "raw" / ("pathmnist.npz" if size == "28" else f"pathmnist_{size}.npz"))
    imgs, labels = npz["test_images"], npz["test_labels"].ravel()
    out = HERE / "gallery"; (out / "img").mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(14)
    parts = ["<!doctype html><meta charset='utf-8'><title>PathMNIST gallery</title>",
             "<style>body{font-family:system-ui,sans-serif;margin:24px;max-width:1100px}"
             "img{width:96px;height:96px;image-rendering:pixelated;margin:2px;border-radius:4px}"
             "h2{margin-bottom:2px}p{margin-top:0;color:#555}</style>",
             "<h1>PathMNIST: colorectal tissue patches</h1>",
             "<p>Test-set patches (patients not in the training set). Labels from the dataset; "
             "these are tissue types, not diagnoses. CC BY 4.0, MedMNIST / NCT-CRC-HE.</p>"]
    for k, name in enumerate(LABELS):
        idx = np.flatnonzero(labels == k)
        pick = rng.choice(idx, size=min(per, idx.size), replace=False)
        parts.append(f"<h2>{k} · {html.escape(name)} <small>({idx.size} in test set)</small></h2><p>{html.escape(GUIDE[k])}</p><div>")
        for i in pick:
            Image.fromarray(imgs[i]).save(out / "img" / f"{k}_{i}.png")
            parts.append(f"<img src='img/{k}_{i}.png' alt='{html.escape(name)} patch {i}' title='test index {i}'>")
        parts.append("</div>")
    (out / "index.html").write_text("\n".join(parts))
    print(f"open {out / 'index.html'} in your browser")

if __name__ == "__main__":
    main()
