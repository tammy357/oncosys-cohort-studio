# Cohort Control Tower · Cohort Firewall

**User:** a research engineer responsible for a dataset that gets new releases.

**Workflow they finish:** connect two releases, see what changed (sample swaps, duplicates, missing measurements), and rerun **only** the analyses those changes affect. AI can summarize what broke and point to the affected records.

## What you get
- Release A: `python data/tcga-brca/fetch.py`
- Practice release B: `python data/tcga-brca/make_release.py`. Same sample count as A, but some samples changed. Check your build with `python data/tcga-brca/make_release.py --verify`.
- Two analyses to protect: `data/tcga-brca/reference_analyses.py`
  - **A1:** ER-alpha protein by clinical ER status (uses clinical + protein)
  - **A2:** RNA subtype mix of primary tumors (uses clinical only)
- The [shared format](../format/README.md). **Required:** your output is what Repair Review Desk teams import.
- `releases/practice-B/CHANGES.json` is the practice answer key. Don't read it until your tool has run.

## Build (weekend MVP)
1. Load two releases and fingerprint every sample: profile hashes, IDs, which files they appear in.
2. Detect changes: swaps, duplicate profiles under new IDs, samples that appeared or disappeared, coverage lost by subgroup.
3. Map each change to the analyses that use that file and those samples. Rerun the affected ones; leave the rest alone and say why.
4. Write `findings.json` (format v0.2) with findings, before and after analysis results, and proposed repairs.

**Stretch:** an AI summary that cites the affected records; a release timeline; alerts in Slack or Discord.

## Live demo
Drop in release B. Your tool catches that membership changed even though the count didn't, identifies A1 as affected, and leaves A2 alone.

## What judges will test
A different release you haven't seen, with different changes. Your tool must find them without being told what to look for. Run it live and produce `findings.json`; the organizers' checker compares your findings with the answer key, and judges see that comparison while they score.

**Suggested stack:** pandas or DuckDB, Streamlit, any LLM for the summary (no keys in the repo).
