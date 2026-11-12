# Warm-up: Breast Cancer Wisconsin (Original)

A 30-minute exercise before the main challenge. It isn't scored.

**The question:** can one number describe 1989–1991? A reviewer wants to publish the malignant share across eight collection groups. Should they report the pooled figure, show the groups alongside it, or pause?

| File | What it is |
|---|---|
| [`starter.ipynb`](starter.ipynb) | Opens with the question, loads the data, draws a first chart |
| `data/wisconsin_readable.csv` | 699 rows with column names; collection groups marked DERIVED |
| `data/data_dictionary.csv` | What each column means and where it came from |
| `data/breast-cancer-wisconsin.data`, `.names` | The official UCI files, unchanged |
| `practice/` | Two altered copies for testing how a tool handles provenance |
| `track1-cohort-firewall/`, `track2-evidence-navigator/` | The original question card, roles and scenario |
| `examples/` | The v0.1 findings format (superseded by [`format/`](../../format/README.md)) |

Check files with `sha256sum -c SHA256SUMS` from this folder.

**Source:** Wolberg, W. (1990). Breast Cancer Wisconsin (Original). UCI Machine Learning Repository. https://doi.org/10.24432/C5HP4Z. CC BY 4.0. De-identified but patient-derived: don't attempt to re-identify anyone.
