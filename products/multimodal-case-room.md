# Multimodal Case Room · Evidence Navigator

**User:** a research team reviewing individual cases.

**Workflow they finish:** open one case and see, in one workspace, its pathology slides, its sample record and its molecular evidence, with an AI-written brief where every statement links to its source. The workspace refuses to attach evidence it can't verify.

## What you get
- [`multimodal-case-room/practice_cases.csv`](multimodal-case-room/practice_cases.csv): 8 practice cases (sample IDs)
- `data/tcga-brca/slide_manifest.csv`: every slide's kind (diagnostic or frozen) and a **browser viewer link**
- `data/tcga-brca/sample_manifest.csv` and the clinical, survival, mutation, copy number and protein files

**The practice cases aren't all clean.** Some have every data layer and a diagnostic slide. At least one has a slide but no record anywhere else, and one is missing a data layer and a diagnostic slide. Your workspace should show what exists, what's missing and why.

## Build (weekend MVP)
1. A case view: sample record, data layers present or absent, slide thumbnails or viewer links, key molecular values (for example ER, PR and HER2 by both clinical test and protein).
2. Strict linking: attach a slide or data point only when its barcode matches the case. Say so plainly when something is missing.
3. An AI brief where every sentence cites its source (field, file, slide), and conflicting evidence is shown, not smoothed over.
4. Export the brief with its links.

**Stretch:** compare two cases side by side; flag cases where the clinical test and the protein measurement disagree.

## Live demo
Open a fully linked case and walk through its evidence. Then open one with a missing link: the workspace shows the gap and refuses to attach the wrong image.

## What judges will test
A case you haven't seen, where the evidence is ambiguous or conflicting.

**Suggested stack:** Streamlit or a small web app, the IDC viewer links, an LLM for the brief.
