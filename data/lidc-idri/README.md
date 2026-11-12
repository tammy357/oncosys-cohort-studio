# Imaging pack: LIDC-IDRI lung CT with radiologist annotations

**One record = one patient's chest CT, where up to four radiologists independently marked lung nodules.** Readers often disagree, which makes this a good place to compare a model's output with human annotations.

## What's here
| File | What it is |
|---|---|
| `patient_manifest.csv` | All 1,010 patients: number of CT series, CT size, number of nodule segmentations (SEG) and reports (SR), license, **browser viewer link** |
| `exercise_patients.csv` | Five patients picked for an optional download exercise (about 230 MB in total) |
| `fetch_exercise.py` | Downloads the exercise CTs with their annotations from NCI Imaging Data Commons |

```bash
pip install idc-index
```
```bash
python data/lidc-idri/fetch_exercise.py --one
```
`--one` fetches only the smallest patient (34 MB; tested); drop it for all five. Everything else stays at the source: open any patient's scan in the browser from `viewer_url`.

## Coverage
1,010 patients with CT; 875 have nodule segmentations. Median CT size is 110 MB, so the full collection (125 GB) is source-linked only.

## What it opens up
A CT viewer that compares a model's nodule output with each radiologist's marks, tools that measure reader agreement, and review queues for nodules where readers disagree.

## What it can't do
LIDC-IDRI patients aren't TCGA or CPTAC patients. **Don't join them.**

## Source and license
NCI Imaging Data Commons collection `lidc_idri`, CC BY 3.0. Cite: Armato, S.G. III et al. The Lung Image Database Consortium (LIDC) and Image Database Resource Initiative (IDRI). *Medical Physics* 38, 915–931 (2011). De-identified: no re-identification, no clinical claims.
