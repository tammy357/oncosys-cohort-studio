# Advanced pack: CAMELYON17 (source-linked)

**One record = one whole-slide image of a breast cancer sentinel lymph node, from one of five Dutch hospitals.** Use it to test whether a model trained at some hospitals still works at another.

This pack is **advanced and optional**: the slides are large (about 1,000 whole-slide images), so they stay at the source.

## What's here
| File | What it is |
|---|---|
| `stages.csv` | Training labels: each patient's pN stage and each slide's label (negative, ITC, micro, macro), with the **center** (0–4) it came from. 600 rows. Copied from the source under CC0. |

## Get the slides (optional)
The slides, annotations and masks are in a public bucket. The publisher's MD5 checksums are in `checksums.md5`.
```bash
curl -O https://camelyon-dataset.s3.amazonaws.com/CAMELYON17/README.md
```
Read that README, then download only the slides you need (for example, one center's training slides) from `https://camelyon-dataset.s3.amazonaws.com/CAMELYON17/images/`.

## What it opens up
Performance across centers: train on four hospitals, test on the fifth, and build a tool that explains what changed.

## What it can't do
These aren't TCGA, CPTAC or PathMNIST patients. **Don't join them.**

## Source and license
CAMELYON17, Computational Pathology Group, Radboud University Medical Center. **CC0.** Cite: Bándi, P. et al. From detection of individual metastases to classification of lymph node status at the patient level: the CAMELYON17 challenge. *IEEE Transactions on Medical Imaging* 38, 550–560 (2019).
