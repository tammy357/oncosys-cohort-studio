"""Measure a research baseline on cell lines from held-out tissue lineages."""
from pathlib import Path
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

df = pd.read_csv(Path(__file__).with_name("starter.csv"))
train, test = (df[df["split"] == split] for split in ("train", "test"))
assert not set(train["lineage"]) & set(test["lineage"])
assert not df["model_id"].duplicated().any()
genes = list(df.columns[3:-1])
target = "kras_gene_effect"
model = make_pipeline(StandardScaler(), Ridge(alpha=1)).fit(train[genes], train[target])
pred = model.predict(test[genes])
mean = [train[target].mean()] * len(test)
print(f"Train: {len(train)}; held out: {len(test)}")
print(f"Ridge MAE: {mean_absolute_error(test[target], pred):.4f}; train-mean MAE: {mean_absolute_error(test[target], mean):.4f}")
for lineage in sorted(test["lineage"].unique()):
    mask = test["lineage"] == lineage
    print(f"{lineage}: n={mask.sum()}, MAE={mean_absolute_error(test.loc[mask, target], pred[mask]):.4f}")
