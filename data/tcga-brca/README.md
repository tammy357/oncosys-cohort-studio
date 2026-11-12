# TCGA-BRCA: one patient, many views

Breast cancer data from The Cancer Genome Atlas, where the **same patients** appear across clinical records, survival, mutations, copy number, RNA, protein and pathology slides. TCGA barcodes link them.

## Get the data (about 3 seconds)
```bash
python data/tcga-brca/fetch.py
```
Use `--no-rna` to skip the 64 MB RNA file. The script downloads from the public UCSC Xena hub into `data/tcga-brca/raw/` (not committed) and checks every file against the event's frozen checksums in `sources.csv`. If a checksum doesn't match, the source has changed: tell the organizers and don't use that file for judged results.

## What's here
| File | What it is | Rows |
|---|---|---|
| `sources.csv` | Each data file: layer, source URL, SHA-256, size, license, retrieval date | 6 |
| `sample_manifest.csv` | **Start here.** One row per sample: patient, sample type, which data layers exist, how many slides | 1,247 |
| `slide_manifest.csv` | One row per pathology slide: barcode, sample, slide kind, size, license, **browser viewer link** | 3,111 |
| `fetch.py` | Downloads and verifies the data files | |

## How the pieces connect
A TCGA barcode such as `TCGA-3C-AAAU-01A` has three parts:
- `TCGA-3C-AAAU` is the **patient** (`patient_id`)
- `TCGA-3C-AAAU-01` is the **sample** (`sample_id`). The two digits are the sample type: `01` primary tumor, `11` normal tissue, `06` metastasis.
- Slide barcodes add more, ending in the slide kind: `DX` diagnostic (FFPE), `TS`/`BS`/`MS` frozen sections.

## Coverage, checked Nov 10, 2026
| | Count |
|---|---|
| Samples / patients | 1,247 / 1,101 |
| Primary tumors / normal tissue / metastases | 1,101 / 139 / 7 |
| Primary tumors with all five data layers (survival, mutation, copy number, RNA, protein) | 535 |
| Pathology slides: diagnostic / frozen | 1,133 / 1,978 |

These numbers are a starting point, not an answer key. Joining layers changes who is in a cohort, and that's part of what you're building tools for.

## Viewing slides
Every row of `slide_manifest.csv` has a `viewer_url` that opens the full slide in NCI Imaging Data Commons' free browser viewer. No download is needed. To download slides (median 338 MB each), use the `idc-index` Python package with the `series_uid`.

## Columns in `sample_manifest.csv`
| Column | Meaning |
|---|---|
| `sample_id` | TCGA sample barcode (15 characters) |
| `patient_id` | TCGA patient barcode (12 characters) |
| `sample_type` | From the barcode's two-digit code |
| `in_clinical` | The sample has a row in `clinical.tsv` |
| `has_survival`, `has_mutation`, `has_copy_number`, `has_rna`, `has_protein` | The sample appears in that file |
| `slides_for_sample` | Number of slides in IDC whose barcode matches this sample |

## Source, license and citation
- Molecular and clinical files: TCGA open-access data as processed by the UCSC Xena TCGA hub. Survival comes from the TCGA Pan-Cancer Clinical Data Resource (Liu et al., *Cell* 2018). **Redistribution terms are being confirmed, so this repository links to the source rather than copying the files.**
- Slides: NCI Imaging Data Commons, collection `tcga_brca`, CC BY 3.0. Credit the TCGA Research Network and NCI Imaging Data Commons.
- The data is de-identified but patient-derived. Don't attempt to re-identify anyone or link it to outside records. No diagnosis, treatment recommendations or clinical claims.
