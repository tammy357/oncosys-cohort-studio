# Cohort Studio · Cohort Firewall

**User:** a researcher who needs a cohort but doesn't write joins for a living.

**Workflow they finish:** describe a cohort in plain English or build it on a visual canvas, see how many records survive each step, catch a join that inflates the count, and export a workflow someone else can rerun.

## What you get
- `data/tcga-brca/sample_manifest.csv`: one row per sample, with patient ID, sample type and which data layers exist
- `data/tcga-brca/slide_manifest.csv`: one row per slide, several per sample
- The clinical, survival and molecular files from `fetch.py`
- How TCGA IDs connect: [data/tcga-brca/README.md](../data/tcga-brca/README.md)

**Known traps in this data:** some patients have more than one sample, most samples have several slides, normal-tissue samples sit next to tumors, and diagnostic and frozen slides are mixed. Joining at the wrong level counts people twice.

## Build (weekend MVP)
1. A request box or canvas. An AI can turn the request into filters and joins, but they must be **editable** before they run.
2. A step-by-step view of how many samples and patients survive each filter and join.
3. A check after every join: did the number of rows grow faster than the number of distinct samples or patients? Show where.
4. Export the final cohort definition as a script or notebook that reproduces it exactly, **plus `cohort_samples.csv`**: one row per sample in the final cohort, with a `sample_id` column (the 15-character TCGA sample barcode). Duplicates in this file mean the inflation wasn't fixed.

**Stretch:** save and compare cohort versions; explain each exclusion in plain words.

## Live demo
Ask for "primary tumors with both RNA and protein data and a diagnostic slide". Add the slide join. The tool shows exactly where the count inflated and lets the user fix it before exporting.

## What judges will test
A different cohort request that inflates through a different join. Run it live and export `cohort_samples.csv`; the organizers' checker compares it with the correct cohort, and judges see that comparison while they score.

**Suggested stack:** DuckDB, Streamlit or a node-based canvas library, an LLM for the plain-English request.
