# Cohort Studio

**Track:** Cohort Firewall · **Product:** Cohort Studio (featured product, with a "who did we lose?" check added)
**Event:** OncoSys Cancer AI Lab, Healthcare AI Hackathon at Northeastern, November 2026

**User:** a cancer researcher who needs a TCGA-BRCA cohort but doesn't write joins for a living.

**The one workflow they finish:** describe a cohort in plain English → check and edit the drafted steps →
see how many rows, samples and patients survive each step → catch any join that counts a sample twice and fix it →
see who the steps removed and get a verdict → export `cohort_samples.csv` and a script that rebuilds the cohort exactly.

> Research use only. Cohort Studio makes no diagnosis, treatment recommendation, risk score or clinical claim.

**Team:** _add names or handles here_ · **Demo video:** _add link here_

---

## Run it (from a fresh clone)

```bash
pip install -r requirements.txt
python data/tcga-brca/fetch.py --no-rna      # downloads the frozen release and checks every checksum
streamlit run app.py                          # the app
```

Or from the command line, which is handy for a judge's new request:

```bash
python -m cohort_studio "primary tumors with both RNA and protein data and a diagnostic slide"
# writes output/cohort_samples.csv, reproduce_cohort.py, cohort_spec.json, ledger.csv, exclusions.csv,
#        sample_selection.csv, coverage.csv, verdict.json and findings.json
python -m cohort_studio --fields              # which fields and values exist in this release
python -m pytest tests -q                     # tests (synthetic fixture + the real manifests)
```

A conversational web app with AI questions, autocomplete, saved searches and favourites is in
[frontend/](frontend/README.md): `pip install -r frontend/requirements.txt`, then `python frontend/server.py`.

Requests that only use sample types, data layers and slides work straight from the committed manifests.
Clinical, survival and mutation filters need `fetch.py` first; until then the tool says
**Insufficient Evidence** and tells you which file is missing.

## What it does, step by step

1. **Request.** Type a cohort in plain English. A built-in parser drafts the steps with no key and no network.
   An AI model can draft them instead (optional, see below). Either way it shows how it read each phrase,
   and lists any words it didn't use.
2. **Editable steps.** Every filter and join appears in a table you can edit before anything runs:
   sample type, data layers, joins (table, key, `rows` or `exists`), filters, gene mutations.
3. **Step-by-step ledger.** Rows, distinct samples and distinct patients after every step, with a plain-words
   reason for every sample removed (`exclusions.csv`).
4. **Inflation check after every join.** If rows grow faster than distinct samples, the step is flagged, with
   example samples and the records they matched, and a one-click fix (keep each sample once, or join on the
   sample barcode instead of the patient barcode). Export is paused until it's fixed.
5. **One sample per patient, with the rule recorded.** Ask for "one sample per patient" and the tool keeps one by a
   stated rule (primary tumor, then metastatic, then normal; ties go to the lowest barcode). `sample_selection.csv`
   lists every patient it had to choose for, what it kept, what it dropped and why. Ask for the "earliest" or "latest"
   sample and it uses sample collection days if the release records them, or says **Insufficient Evidence** if not.
6. **Who did we lose?** For every step, how the mix of submitting site, PAM50 subtype, ER status, stage and age
   band changed (total variation distance). Fields you filtered on are left out, so choosing ER-positive tumors
   isn't reported as a bias, and shifts that follow from them (an ER filter changes the PAM50 mix) are marked
   "expected", with the reason. Other shifts, such as submitting site, still need a person to look.
7. **What's recorded for this cohort.** Every clinical and survival variable in the release, with the share of the
   cohort's samples that have a value, so you can see what's usable before you filter or model on it (`coverage.csv`).
8. **Verdict:** Ready, Conditional, Blocked or Insufficient Evidence, with every check, the fields used, the
   decision rule, what a person must review, and the limitations. See [VERDICT_RULES.md](VERDICT_RULES.md).
9. **Export.** `cohort_samples.csv` (one row per sample, `sample_id` column, no duplicates),
   `reproduce_cohort.py` (rebuilds the cohort with the same operations and checks the files' checksums),
   `cohort_spec.json`, `ledger.csv`, `exclusions.csv`, `sample_selection.csv`, `coverage.csv`, `verdict.json` and
   `findings.json` in the shared format v0.2.
10. **Compare versions** (stretch): save two cohorts and see what's only in each.

## One result you can verify

The brief's live demo, **"primary tumors with both RNA and protein data and a diagnostic slide"**, on the frozen release:

| step | rows | samples | patients |
|---|---:|---:|---:|
| Keep primary tumor samples | 1,101 | 1,101 | 1,101 |
| Require RNA expression data | 1,097 | 1,097 | 1,097 |
| Require protein (RPPA) data | 742 | 742 | 742 |
| Join diagnostic (FFPE) slides, one row per slide | **776** | **731** | 731 |
| After the fix: keep each sample once if it has a diagnostic slide | 731 | 731 | 731 |

45 samples have two diagnostic slides (for example `TCGA-A7-A0CD-01`: `…-DX1` and `…-DX2`), so the naive join
counts them twice. Check it in a few lines:

```python
import pandas as pd
m = pd.read_csv("data/tcga-brca/sample_manifest.csv"); s = pd.read_csv("data/tcga-brca/slide_manifest.csv")
t = m[(m.sample_type == "Primary Tumor") & m.has_rna & m.has_protein]
j = t.merge(s[s.slide_kind == "diagnostic (FFPE)"], on="sample_id")
print(len(t), len(j), j.sample_id.nunique())   # 742 776 731
```

Joining every slide kind instead gives 1,877 rows for 742 samples.

## One limitation you can inspect

The app's **Limitations** panel and the verdict show it for every cohort. For this one: requiring protein data
removes 355 primary tumors and shifts the mix of submitting sites (TVD 0.14), so the verdict is **Conditional**.
The tool can show who was removed, but it can't say why those samples have no protein data. The 0.10 threshold
is a convention, not a validated cut-off.

## When a person must review

Every verdict lists what a person must check before the cohort is used, for example confirming the steps match the
intended question, deciding whether a shifted group matters, or confirming that unused words in the request don't
change the cohort.

## AI features

- **Off by default.** The built-in parser needs no key, so the tool runs from a fresh clone.
- **When it's on,** the model only drafts the editable steps. It never sees patient-level data and never produces
  a number: every count comes from the engine running on the frozen files.
- **Grounded, and it asks instead of guessing** (`cohort_studio/assistant/`). The model is given the release's
  real fields, values and counts, and every step it writes is checked against the data. When a choice would
  change the cohort (for example what "hormone-receptor positive" means), it asks, showing how many samples each
  answer gives. The web app asks you; the Streamlit app and the CLI use the recommended answer and leave it for a
  person to confirm.
- **Keys come from the user:** paste your own key in the sidebar (not stored), or set `ANTHROPIC_API_KEY`.
- **Local models work too,** through any OpenAI-compatible endpoint, e.g.
  `OPENAI_BASE_URL=http://localhost:11434/v1 COHORT_STUDIO_MODEL=llama3.1`.
- **No keys are in this repository.**
- **When the data can't answer,** the tool says so: missing files, missing fields, unknown genes or unused words
  lead to Insufficient Evidence or Blocked, with the reason.

No model is trained, so no model card is needed.

## Data

Judged results use only the event's frozen TCGA-BRCA release: the files `data/tcga-brca/fetch.py` downloads
(checked against `data/tcga-brca/sources.csv`) and the committed manifests. Nothing is linked to outside sources,
and no raw TCGA files are committed. The app shows the checksum status of each file, and `reproduce_cohort.py`
warns if a file differs from the one the cohort was built from.

Credits: The results shown here are in whole or part based upon data generated by the TCGA Research Network
(https://www.cancer.gov/tcga), via the UCSC Xena TCGA hub (Goldman et al., *Nat Biotechnol* 2020), with curated
survival from Liu et al., *Cell* 2018, and slide metadata from NCI Imaging Data Commons (CC BY 3.0). See
[DATA_LICENSES.md](DATA_LICENSES.md).

## Code layout

| Path | What it is |
|---|---|
| `app.py` | Streamlit app |
| `cohort_studio/ops.py` | The data operations, shared by the app and the exported script |
| `cohort_studio/parse.py` | Plain English → steps, without AI |
| `cohort_studio/llm.py` | Optional AI drafting for the Streamlit app and CLI (user's key or a local model) |
| `cohort_studio/assistant/` | The grounded AI route: release catalogue, prompt, step checks, questions with counts |
| `frontend/` | Web app: FastAPI server, single-page interface, SQLite/Postgres storage |
| `cohort_studio/engine.py` | Runs the steps, records the ledger, detects and fixes inflation, measures shift |
| `cohort_studio/verdict.py` | The verdict and its checks |
| `cohort_studio/exports.py` | `cohort_samples.csv`, reproduce script, findings.json and the rest |
| `tests/` | Tests on a synthetic fixture (made-up `TCGA-ZZ-…` barcodes) and the real manifests |
| `CHALLENGE_README.md` | The organizers' original README |

## Third-party code

pandas (BSD-3-Clause), Streamlit (Apache-2.0), Matplotlib (Matplotlib/PSF licence), jsonschema (MIT),
pytest (MIT). Web app: FastAPI (MIT), Uvicorn (BSD-3-Clause), SQLAlchemy (MIT), HTTPX (BSD-3-Clause). The organizers' challenge files (`data/`, `format/`, `starter/` and others) are used as provided.
