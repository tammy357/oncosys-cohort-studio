# Explore the data

TCGA-BRCA remains the core pack for the six featured missions. Other packs are available for practice and custom builds; follow the event rules and confirm with organizers before using supplemental data for scoring. Start with the included compact files where available.

| Pack | One record is… | Laptop download | What it opens up | Status |
|---|---|---|---|---|
| **[TCGA breast cancer](tcga-brca/README.md)** *(core: featured missions)* | A tumor sample with clinical, survival, mutation, copy number, RNA and protein data, plus slide links | ~70 MB | Cohort tools, release monitors, case workspaces, evidence assistants | **Ready** |
| **[Pathology: PathMNIST](pathmnist/README.md)** | A labelled colorectal tissue patch | 205.6 MB (28 × 28) | Tissue classification, visual search and review queues | **Ready: download, gallery and baseline verified** |
| **[Molecular + imaging: CPTAC](cptac/README.md)** | A lung, colon or kidney cancer case | 24 kB protein starter; slide and CT manifests <1 MB | Build a case workspace and inspect protein/slide coverage | **Ready: protein, slides and CT links** |
| **[Imaging: LIDC-IDRI](lidc-idri/README.md)** | A chest CT with up to 4 radiologists' nodule marks | Optional 34 to 230 MB exercise | Compare model output with reviewer annotations | **Ready** |
| **[Prediction: DepMap](depmap/README.md)** | A cancer cell-line model | 101 kB starter | Predict KRAS knockout gene effect and audit performance by lineage | **Ready** |
| **[Trial records: ClinicalTrials.gov](clinical-trials/README.md)** | A dated registry snapshot or a separately authored fictional case | <1 MB for three registry records; replay fixture is included | Inspect registry criteria and replay an explicitly fictional rule change | **Ready for practice** |
| **[Advanced: CAMELYON17](camelyon17/README.md)** | A lymph-node whole-slide image from one of 5 hospitals | Labels only; slides source-linked | Test performance across hospitals | **Ready (source-linked)** |
| **[Warm-up: Wisconsin](../warmup/wisconsin/README.md)** | A breast-mass sample scored 1 to 10 on nine features | < 1 MB | A 30-minute tabular practice | **Ready** |

## The joining rule
These packs describe **different things**: tissue patches, cell lines, tumor samples, CT scans, trial records. **Only join records where a documented match exists**, such as TCGA barcodes within TCGA, or CPTAC case IDs with a verified mapping. Never join across packs because IDs or labels look similar. When a match is missing, show the gap: that's a finding, not a failure.

## How every pack is built
Each folder has the same pieces, so every source is documented and checkable the same way:
1. **`README.md`:** what one record means, how files connect, coverage, license, citation, and what the data can't support.
2. **`sources.csv`:** each file's URL, checksum, size, license and retrieval date.
3. **A fetch script** that downloads into `raw/` (not committed) and stops if a checksum doesn't match.
4. **Small manifests, committed:** tables that link the pieces (samples, images, versions), with viewer links where images stay at the source.

## Adding a pack
Make `data/<pack>/` with the four pieces above, and add `data/<pack>/raw/` to `.gitignore`.
- **Commit** only small files whose license clearly allows redistribution, such as CC0 or CC BY with attribution.
- **Fetch, don't commit,** anything large or where redistribution isn't confirmed.
- **Never** commit controlled-access data, non-commercial-licensed data, credentials, or personal identifiers.
