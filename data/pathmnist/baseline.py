"""A simple CPU baseline: colour-histogram features + logistic regression. Writes sample AI outputs.

Usage (from the repo root, after fetch.py):  python data/pathmnist/baseline.py [--size 28|64|128|224]
Output: data/pathmnist/outputs/test_predictions.csv  (one row per test patch: true label, predicted label,
        probability for each class, confidence) and review_queue.csv (the least confident predictions).
Runs in a few minutes on a laptop. It is deliberately simple: a starting point to beat, explain or stress-test.
"""
import pathlib, sys
import numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score

HERE = pathlib.Path(__file__).resolve().parent
LABELS = ["adipose", "background", "debris", "lymphocytes", "mucus", "smooth muscle",
          "normal colon mucosa", "cancer-associated stroma", "colorectal adenocarcinoma epithelium"]

def features(imgs, bins=16):
    x = imgs.reshape(len(imgs), -1, 3)
    hist = [np.stack([np.histogram(px[:, c], bins=bins, range=(0, 256))[0] for c in range(3)]).ravel() for px in x]
    return np.hstack([np.array(hist) / x.shape[1], x.mean(axis=1) / 255, x.std(axis=1) / 255])

def main():
    size = sys.argv[sys.argv.index("--size") + 1] if "--size" in sys.argv else "28"
    d = np.load(HERE / "raw" / ("pathmnist.npz" if size == "28" else f"pathmnist_{size}.npz"))
    xtr, ytr = features(d["train_images"]), d["train_labels"].ravel()
    xte, yte = features(d["test_images"]), d["test_labels"].ravel()
    model = LogisticRegression(max_iter=2000).fit(xtr, ytr)
    proba = model.predict_proba(xte)
    pred = proba.argmax(axis=1)
    out = HERE / "outputs"; out.mkdir(exist_ok=True)
    df = pd.DataFrame({"test_index": np.arange(len(yte)), "true_label": [LABELS[i] for i in yte],
                       "predicted_label": [LABELS[i] for i in pred], "confidence": proba.max(axis=1).round(4)})
    for k, name in enumerate(LABELS):
        df[f"p_{name.replace(' ', '_')}"] = proba[:, k].round(4)
    df.to_csv(out / "test_predictions.csv", index=False)
    df.sort_values("confidence").head(200).to_csv(out / "review_queue.csv", index=False)
    print(f"test accuracy {accuracy_score(yte, pred):.3f} on {len(yte)} patches from patients not used in training")
    print(f"wrote {out / 'test_predictions.csv'} and review_queue.csv (200 least confident)")

if __name__ == "__main__":
    main()
