# Molecular and imaging pack: CPTAC (lung, colon, kidney)

**Ready.** Start with [protein_starter.csv](protein_starter.csv): six protein measurements across 306 tumor cases (106 lung, 97 colon, 103 kidney), about 24 kB. One row is one case in a cohort. The source files describe the values as log2 reference-intensity-normalized gene-level protein abundance. Blank means not measured or unavailable in the extract; values are rounded to four decimal places.

| File | What it contains |
|---|---|
| [protein_starter.csv](protein_starter.csv) | EGFR, ERBB2, KRAS, TP53, PTEN and GAPDH; no slide downloads needed |
| [slide_manifest.csv](slide_manifest.csv) | 2,291 slide records with cohort, case, slide, license and viewer link |
| [lung_ct_manifest.csv](lung_ct_manifest.csv) | CT studies with viewer links for 37 lung cases |
| [protein_sources.csv](protein_sources.csv) | Three frozen BCM tumor protein matrices, download URLs and SHA-256 hashes |

**Join on both `cohort` and `case_id`, keeping unmatched image cases visible.** All 306 protein cases have an exact case-level match in the slide manifest, including colon cases that use legacy IDs. This establishes case-level linkage, not that a slide and protein measurement came from the same tissue aliquot. Multiple slides can match one case. Do not join across cohorts or match by a similar-looking ID.

**Build:** a case workspace that opens the source slide, shows available protein measurements, and warns when there is no matching protein case. Compare coverage before and after the join. RNA is not part of this starter. Do not infer RNA–protein agreement or clinical conclusions from it.

Slides and CT stay at [NCI Imaging Data Commons](https://portal.imaging.datacommons.cancer.gov/). The browser viewer URLs are in the manifests. Check the specific file's terms before redistributing images. To rebuild the starter, run `python data/cptac/protein.py` from the repo root; it downloads about 21 MB of frozen BCM matrices into ignored `raw/` and verifies checksums.

Credit: Clinical Proteomic Tumor Analysis Consortium (CPTAC), the Baylor College of Medicine processed matrices distributed with [cptac on Zenodo](https://doi.org/10.5281/zenodo.8394329) under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), and NCI Imaging Data Commons. The starter selects six proteins, reshapes the matrices and rounds values. Patient-derived data: no re-identification or clinical use.
