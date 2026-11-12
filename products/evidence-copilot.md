# Evidence Copilot · Evidence Navigator

**User:** a scientist who wants an answer from the data in plain language without trusting a black box.

**Workflow they finish:** ask a question; the assistant runs a **bounded** analysis on the real files and returns an answer with a chart, the data release, the filters, the sample count and the calculation attached. It says what it couldn't establish, and refuses questions the data can't answer.

## What you get
- The TCGA-BRCA files from `fetch.py`, the manifests, and `reference_analyses.py` as an example of a bounded analysis
- Practice questions to build against:
  1. Is ER-alpha protein higher in tumors that tested ER-positive? Does that hold if you only use samples with every data layer?
  2. Which tumors disagree between the clinical ER test and the ER-alpha protein measurement?
  3. Does HER2 protein differ by the clinical HER2 test result?
  4. How many primary tumors have both RNA and protein data?
  5. Which treatment works best for Luminal B tumors? (The data can't answer this. Your copilot should say so and explain why.)

## Build (weekend MVP)
1. A chat or question box. The assistant uses tools that run real queries in a sandbox (pandas or DuckDB), never answers from memory.
2. Every answer carries its provenance: release and checksums, filters, sample count, the code or query that ran.
3. "Rerun" and "change a filter" buttons on the same screen.
4. Clear refusals, saying what's missing, for questions the data can't support.
5. A small evaluation: your practice questions plus your own, with a score for correct answers and correct refusals.

**Stretch:** a Slack or Discord bot; side-by-side comparison of two inclusion rules.

## Live demo
Ask question 1. The judge follows the answer back to the source and reruns it with a changed inclusion rule from the same screen.

## What judges will test
Three questions you haven't seen, including one the copilot should refuse.

**Suggested stack:** any LLM with tool use or a local model (no keys in the repo), DuckDB, Streamlit or Gradio chat.
