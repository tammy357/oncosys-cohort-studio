# Cohort Studio

[![tests](https://github.com/tammy357/oncosys-cohort-studio/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/tammy357/oncosys-cohort-studio/actions/workflows/tests.yml)

**Track:** Cohort Firewall · **Product:** Cohort Studio (featured product, with a "who did we lose?" check added)
**Event:** OncoSys Cancer AI Lab, Healthcare AI Hackathon at Northeastern, November 2026

**User:** a cancer researcher who needs a TCGA-BRCA cohort but doesn't write joins for a living.

**The one workflow they finish:** describe a cohort in plain English → check and edit the drafted steps →
see how many rows, samples and patients survive each step → catch any join that counts a sample twice and fix it →
see who the steps removed and get a verdict → export `cohort_samples.csv` and a script that rebuilds the cohort exactly.

> Research use only. Cohort Studio makes no diagnosis, treatment recommendation, risk score or clinical claim.

**Team:**
| Name | GitHub |
|---|---|
| Lily Tammy | [@tammy357](https://github.com/tammy357) |
| Seth Barribeau | [@sethbarr](https://github.com/sethbarr) |
| Hriday Desai | [@dogtags27](https://github.com/dogtags27) |
| Aayam Raj Shakya | [@aayamrajshakya](https://github.com/aayamrajshakya) |
| Yanyun Ma | [@yanyun1112](https://github.com/yanyun1112) |

**Demo video:** _add link here_

---

## Quick start (from a fresh clone, about 5 minutes)

Tested with Python 3.13, 3.14 and 3.15 on macOS and Linux. On some systems the command is `python3` instead of `python`.

**1. Get the code and a clean environment**

```bash
git clone https://github.com/tammy357/oncosys-cohort-studio.git
cd oncosys-cohort-studio
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
```

**2. Install.** Cohort Studio only needs a few packages:

```bash
pip install "pandas>=2.0" matplotlib jsonschema "streamlit>=1.50" pytest
```

(`pip install -r requirements.txt` also works and adds the organizers' notebook and imaging packages, which
Cohort Studio doesn't use. It can take around 10 minutes.)

**3. Download the frozen data** (about 5 MB, needs internet access):

```bash
python data/tcga-brca/fetch.py --no-rna
```

Every file should end with `ok`. This checks each download against the event's frozen checksums.

**4. Run the brief's demo request from the command line** (this is the path a judge's new request takes):

```bash
python -m cohort_studio "primary tumors with both RNA and protein data and a diagnostic slide"
```

With internet access the steps are drafted by AI: your own key if you've set one, otherwise the hosted Cohort Studio
service. Without internet the built-in
parser drafts them. `Drafted by:` says which. Add `--rules` to use only the built-in parser.

You should see step 4 flagged (`776` rows for `731` samples), the fix applied, then:

```
VERDICT: Conditional
...
Wrote 731 samples to output/cohort_samples.csv (plus reproduce_cohort.py, cohort_spec.json, ...)
```

The cohort is in `output/cohort_samples.csv` (one `sample_id` per row, no duplicates). Any plain-English request
works the same way, for example `python -m cohort_studio "ER-positive tumors"` (808 samples, Ready).

**5. Open the app**

```bash
streamlit run app.py
```

It opens in your browser. Click the first example, then **Draft the steps** → **Run the steps** →
**Apply this fix to step 4**, and the verdict changes from Blocked to Conditional.

**Other useful commands**

```bash
python -m cohort_studio --fields                     # which fields and values exist in this release
python output/reproduce_cohort.py data/tcga-brca output/rebuilt.csv   # rebuild a saved cohort from the files
python format/validate.py output/findings.json       # check findings.json against the shared format
python -m pytest tests -q                            # tests (synthetic fixture + the real manifests)
```

### On the day: the sealed envelope

The judges' test is a new cohort request in a sealed envelope. Open it with the organizers' script (it checks
release A first, so run `fetch.py` beforehand), then point Cohort Studio at the request file:

```bash
python envelopes/unseal.py 07 amber falcon river quartz            # the number and four words from your card
python -m cohort_studio --file envelopes/opened/E07/cohort_studio_request.md --out output/E07
```

The tool prints the request it read, every step with its counts, any join that counts samples twice and the fix,
and the verdict, then writes `output/E07/cohort_samples.csv` (one `sample_id` per row) for the checker. Add
`--rules` to use only the built-in parser, with nothing leaving the computer. If the file's layout is unexpected,
paste the request itself in quotes instead: `python -m cohort_studio "the request" --out output/E07`.

Rehearse with the practice envelope (`python envelopes/unseal.py practice orange meadow pilot lantern`). It holds
practice release B for the Control Tower track, not a cohort request, so it checks that unsealing works on your
machine.

### If something goes wrong

- **`fetch.py` fails with a connection error:** your network may block the download hosts. Requests that only use
  sample types, data layers and slides still work from the committed manifests. Clinical, survival and mutation
  filters will say **Insufficient Evidence** and name the missing file, rather than guessing.
- **`ModuleNotFoundError`:** the virtual environment isn't active. Run `source .venv/bin/activate` again, then retry.
- **`streamlit: command not found`:** use `python -m streamlit run app.py`.
- **Streamlit suggests installing Watchdog or running `xcode-select --install` (macOS):** you can ignore it. It's an optional speed-up for reloading code, not something the app needs.
- **Port already in use:** `streamlit run app.py --server.port 8502`.

A conversational web app with AI questions, autocomplete, saved searches and favourites is in
[frontend/](frontend/README.md): 
```bash
pip install -r frontend/requirements.txt
python frontend/server.py
```
Requests that only use sample types, data layers and slides work straight from the committed manifests.
Clinical, survival and mutation filters need `fetch.py` first; until then the tool says
**Insufficient Evidence** and tells you which file is missing.

## How to use Cohort Studio

A researcher or team says which TCGA-BRCA samples they need, in plain English or as a JSON cohort definition, and
gets back **`cohort_samples.csv`** (one row per sample, a `sample_id` column with the 15-character TCGA sample
barcode, no duplicates) and **`reproduce_cohort.py`**, a script that rebuilds exactly that cohort from the frozen
files.

| Where | Best for | How |
|---|---|---|
| **Web app: <https://oncosys-cohort-studio-static.vercel.app/>** | The main way to use it: type a request, answer the AI's questions, check and edit the steps, fix any inflating join, compare versions, export | Open the link; nothing to install |
| **Command line** | One request, or a whole file of them (the path a judge's new request can take) | `python -m cohort_studio "..."` (see [Quick start](#quick-start-from-a-fresh-clone-about-5-minutes)) |
| **A file or your own Python** | Many cohorts at once, or Cohort Studio inside an analysis | `--file`, or `cohort_studio.api.build_cohort` |
| **AI agents (MCP)** | Claude, Cursor or any MCP client building cohorts as tools | Add `https://api.maplestreettax.site/mcp` (see [For AI agents](#for-ai-agents-mcp)) |
| Streamlit app, local web app | Working offline, development | `streamlit run app.py`, `python frontend/server.py` |

### How the services fit together

- **Vercel** serves the web app's pages and forwards every `/api/...` call to the backend on AWS.
- **AWS** (one EC2 server, `api.maplestreettax.site`) runs the backend ([`frontend/server.py`](frontend/server.py)): the
  web app's API, the hosted AI service (`/api/v1`) and the MCP server (`/mcp`). It holds the model key and its own copy
  of the frozen release. Its database (a SQLite file on the server's disk) keeps saved searches, a cache of model
  replies (the same request gets the same draft) and the AI usage counts.
- **Your computer** (a clone, for the command line, files, Python, Streamlit or the local MCP server) runs the engine
  on your own copy of the data. It asks the AWS backend only to draft a plain-English request, and only when you
  have no model key of your own.
- **The model provider** is called only by the backend, with the team's key, or directly by your own key.

### What you can send

```bash
# plain English
python -m cohort_studio "ER-positive primary tumours with a diagnostic slide"

# a JSON cohort definition (the same format as cohort_spec.json in every export), run exactly as written
python -m cohort_studio --spec my_cohort.json

# a file of requests and/or definitions: one output folder per cohort, plus summary.csv
python -m cohort_studio --file examples/cohorts.json --out output/batch
```

A `.txt` file holds one request per line. A `.json` file holds one query or a list (or `{"cohorts": [...]}`), where
each query is a request string, `{"request": "..."}`, or a definition with `"steps"`; an optional `"name"` names its
folder. See [`examples/cohorts.json`](examples/cohorts.json).

From Python (or edit and run [`examples/run_query.py`](examples/run_query.py)):

```python
from cohort_studio.api import build_cohort

run = build_cohort("primary tumors with both RNA and protein data and a diagnostic slide", out_dir="output/demo")
print(run.n_samples, run.verdict.verdict, run.route["label"])
print(run.out_dir / "cohort_samples.csv")
```

### Who drafts the steps

Every local route (command line, files, Python, Streamlit) tries, in order:

1. **Your own model:** `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`, set in the environment or in a `.env` file at the
   repository root (git ignores it), or a local model through `OPENAI_BASE_URL` (see
   [Ollama](#run-with-a-local-model-ollama)).
2. **Otherwise the hosted Cohort Studio AI service**, the team's deployment, which holds the model key. It receives
   only the request text, never data files or results, and has [daily limits](#limits-on-the-hosted-ai-service).
3. **Otherwise the built-in parser,** which needs no key and no network. This is also what happens when you're
   offline, the service can't be reached or is over its limit, or an AI draft can't run on your copy of the data.

The output always says which one drafted the steps (`Drafted by: ...` and a note in `cohort_spec.json`).
`--rules` uses only the built-in parser, so nothing leaves your computer. `--no-hosted` (or
`COHORT_STUDIO_HOSTED=off`) allows your own model but never the hosted service.

**The AI draft is checked against the built-in parser.** The parser drafts the same request too. If both select
the same samples, that's it. If they differ, one short extra AI call compares the two drafts, with their sample
counts and any words the parser skipped. It keeps the AI draft, the parser's draft, or a mix of their steps; a mix
may only reuse steps from the two drafts. The notes say which one was kept and why. Whatever is kept, every step is
checked against the release before it runs, and every count comes from the engine, never from the model.

### What you get

Each cohort's output folder (default `output/`) contains:

| File | What it is |
|---|---|
| `cohort_samples.csv` | One row per sample in the final cohort, with a `sample_id` column (the 15-character TCGA sample barcode). Never a duplicate: inflating joins are fixed before export (`--no-fix` only shows the problem). |
| `reproduce_cohort.py` | Rebuilds the cohort exactly from the frozen files: `python output/reproduce_cohort.py data/tcga-brca output/rebuilt.csv` |
| `cohort_spec.json` | The cohort definition; rerun it with `--spec` |
| `ledger.csv`, `exclusions.csv`, `sample_selection.csv`, `coverage.csv` | Every step's rows, samples and patients; every removed sample and why; one-per-patient choices; what's recorded for the cohort |
| `verdict.json`, `findings.json` | The verdict and its checks, in the event's shared format |

### For AI agents (MCP)

Cohort Studio is also an [MCP](https://modelcontextprotocol.io) server, so an AI agent can build cohorts as tools:

| Tool | What it does |
|---|---|
| `describe_release` | Sample types, data layers, slide kinds, every field's exact values and counts, and the step format |
| `draft_cohort` | Plain English → an editable cohort definition (JSON steps, and each step in words), not run |
| `build_cohort` | A request or a definition → each step's rows, samples and patients, any inflating join fixed, the verdict, the sample IDs and the final definition |
| `get_cohort_file` | A definition → `cohort_samples.csv`, `reproduce_cohort.py` or any other export |

**Hosted (nothing to install):** add `https://api.maplestreettax.site/mcp` as a remote MCP server
(streamable HTTP). Plain-English requests are drafted by the service's model within the
[limits](#limits-on-the-hosted-ai-service), and by the built-in parser beyond them.

```bash
claude mcp add --transport http cohort-studio https://api.maplestreettax.site/mcp
# with a personal token: add  --header "Authorization: Bearer $COHORT_STUDIO_API_TOKEN"
```

In Claude's apps, add the same URL as a custom connector.

**Local (from a clone, on your copy of the data):** a stdio server that drafts like the command line (your key, else
the hosted service, else the parser; `--rules` for the parser only).

```bash
claude mcp add cohort-studio -e PYTHONPATH="$PWD" -- python -m cohort_studio.mcp_server
```

For clients configured with JSON (e.g. Claude Desktop):
`{"mcpServers": {"cohort-studio": {"command": "python", "args": ["-m", "cohort_studio.mcp_server"],
"env": {"PYTHONPATH": "/path/to/oncosys-cohort-studio"}}}}`.

### Limits on the hosted AI service

| Limit | Default |
|---|---|
| AI requests per person (browser or command-line user) per day | 500 |
| AI requests per network address per day (everyone behind it shares this) | 1,000 |
| AI requests per person per minute | 10 |
| Estimated model spend for the whole service per day | $4; after that everyone gets the built-in parser until 00:00 UTC |

A personal token raises your daily number to 1,000. To use one, set `COHORT_STUDIO_API_TOKEN` in your environment or
your git-ignored `.env`, and never commit it. Ask the team for a token. Over a limit nothing breaks: the built-in
parser drafts the steps and the output says why. Your own key is never limited by the service.

The hosted service runs on the team's own server on AWS, at `api.maplestreettax.site`. The domain name isn't related to the project; it's simply the address the team's server uses.

## What it does, step by step

1. **Request.** Type a cohort in plain English, or give a JSON cohort definition. An AI model drafts the steps when
   one is available ([who drafts](#who-drafts-the-steps)), checked against the built-in parser, which drafts them
   with no key and no network otherwise. Either way it shows how it read each phrase, and lists any words it didn't
   use.
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

- **On whenever it can be.** Your own key or local model first, otherwise the hosted Cohort Studio service, otherwise
  the built-in parser (see [Who drafts the steps](#who-drafts-the-steps)). With no internet access the tool still
  runs from a fresh clone, using the built-in parser.
- **The model only drafts the editable steps.** It never sees patient-level data and never produces a number: every
  count comes from the engine running on the frozen files.
- **Checked against the built-in parser.** When the AI's and the parser's drafts select different samples, a short
  second call keeps one of them or a mix of their steps, and says why
  ([`cohort_studio/assistant/reconcile.py`](cohort_studio/assistant/reconcile.py)).
- **Grounded, and it asks instead of guessing** (`cohort_studio/assistant/`). The model is given the release's
  real fields, values and counts, and every step it writes is checked against the data. When a choice would
  change the cohort (for example what "hormone-receptor positive" means), it asks, showing how many samples each
  answer gives. The web app asks you; the Streamlit app and the CLI use the recommended answer and leave it for a
  person to confirm.
- **Your own key, or the team's service:** paste your own key in the Streamlit sidebar (not stored), or set
  `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` (environment or `.env`). Without one, the hosted service drafts within its
  [limits](#limits-on-the-hosted-ai-service). See [Run with a hosted model (your own key)](#run-with-a-hosted-model-your-own-key) below.
- **Local models work too,** through any OpenAI-compatible endpoint. See
  [Run with a local model (Ollama)](#run-with-a-local-model-ollama) below.
- **No keys are in this repository.**
- **When the data can't answer,** the tool says so: missing files, missing fields, unknown genes or unused words
  lead to Insufficient Evidence or Blocked, with the reason.

No model is trained, so no model card is needed.

### Run with a hosted model (your own key)

Likely more accurate than a small local model (we haven't measured it on the held-out set yet), and nothing to install: the AI step calls the provider directly. You need your own API
key from [Anthropic](https://console.anthropic.com/) or [OpenAI](https://platform.openai.com/api-keys), and each
request costs a small amount on that key.

| Provider | Default model | Key variable | Pick another model with |
|---|---|---|---|
| Anthropic | `claude-haiku-5-5` | `ANTHROPIC_API_KEY` | `COHORT_STUDIO_MODEL=...` or the sidebar's Model field |
| OpenAI | `gpt-4.1-mini` | `OPENAI_API_KEY` | `COHORT_STUDIO_MODEL=...` or the sidebar's Model field |

- **Streamlit app:** start it with `streamlit run app.py`. In the sidebar, keep `Draft steps with AI` on, open
  `Use your own key or a local model`, choose the `Provider` and paste your key. Leave `Model` and `Base URL` empty
  to use the default. The key stays in your browser session and isn't saved.
- **Command line:** set the key in the environment (or put it in `.env`) and run as usual. `--ai` names the provider
  when both keys are set.

  ```bash
  ANTHROPIC_API_KEY=sk-ant-... python -m cohort_studio "ER-positive tumors"
  OPENAI_API_KEY=sk-...        python -m cohort_studio --ai openai "ER-positive tumors"
  ```

- **Web app:** copy the template with `cp .env.example .env` and uncomment your key line in `.env` (e.g.
  `OPENAI_API_KEY=sk-...`), or set the key in the environment, then start `python frontend/server.py`. Add `COHORT_STUDIO_PROVIDER=anthropic` to use
  Claude. The server stops calling the model after 200 calls per run (`COHORT_STUDIO_AI_CALL_LIMIT`), and the
  [daily limits](#running-the-hosted-ai-service) apply too, so a low-balance key can't be drained by accident.

If the provider can't be reached, or the key is wrong or out of credit, the app says so and the built-in parser
drafts the steps instead.

### Run with a local model (Ollama)

Free and private (nothing leaves your computer), with no key. It's slower and less accurate than a hosted model.

> [!NOTE]
> We tested Cohort Studio with `qwen2.5:7b` on our laptop, so the steps below use that model. Any model Ollama
> can run should work: replace `qwen2.5:7b` with its name everywhere. Speed and accuracy will differ, and the
> benchmark (see the end of this section) is how to compare.

**1. One-time setup.** Install Ollama from [ollama.com](https://ollama.com), then download the model we tested
(~4.7 GB):

```bash
ollama pull qwen2.5:7b
```

**2. Start Ollama with enough room for the AI's instructions.** Leave this terminal open:

```bash
OLLAMA_CONTEXT_LENGTH=8192 OLLAMA_KEEP_ALIVE=60m ollama serve
```

- `OLLAMA_CONTEXT_LENGTH=8192`: the AI's instructions take about 3,500 of Ollama's default 4,096 tokens, so a
  longer answer (for example one with questions) would be cut off and the AI step would fail.
- `OLLAMA_KEEP_ALIVE=60m`: keeps the model loaded between requests instead of reloading it each time.

> [!IMPORTANT]
> If Ollama already runs in the background (the Mac and Windows apps and the Linux installer start it
> automatically), these settings won't apply. Quit it first (on Linux: `sudo systemctl stop ollama`), then run the
> command above.

**3. Check it's ready.** Load the model, then look at where it runs:

```bash
curl -s http://localhost:11434/api/generate -d '{"model": "qwen2.5:7b", "keep_alive": "60m"}' > /dev/null
ollama ps          # PROCESSOR should say 100% GPU
```

> [!TIP]
> If it says CPU or splits between CPU and GPU, close other apps that use the graphics card (browser tabs, video)
> and check again. On the CPU the model is usually too slow to answer in time.

**4. Use it.**

- **Streamlit app:** keep Ollama running, and in a second terminal start the app with `streamlit run app.py`.
  Then in the sidebar, turn on `Draft steps with an AI model`, set Provider to `openai`, leave the key empty, set Model to `qwen2.5:7b` and Base URL to `http://localhost:11434/v1`.
- **Command line:**

  ```bash
  OPENAI_BASE_URL=http://localhost:11434/v1 COHORT_STUDIO_MODEL=qwen2.5:7b \
      python -m cohort_studio --no-hosted "ER-positive tumors"
  ```

- **Web app:**

  ```bash
  COHORT_STUDIO_PROVIDER=openai OPENAI_BASE_URL=http://localhost:11434/v1 COHORT_STUDIO_MODEL=qwen2.5:7b \
      python frontend/server.py
  ```

**What to expect** (from our tests with `qwen2.5:7b`)

- Each request took about 30–90 seconds on the tested laptop, and the first one is slower while the model loads. The
  AI step gives up after 90 seconds (the `timeout=90` default of `LLMClient` in
  [`cohort_studio/assistant/client.py`](cohort_studio/assistant/client.py#L54C100-L54C102)); the
  built-in parser then drafts the steps and the app says so.
- On the 20 held-out benchmark cases it got 9 exactly right. It's a small model; a hosted model (with your own
  key) is likely more accurate, though we haven't measured one on the held-out set yet.

> [!CAUTION]
> In our tests its most common mistake was adding a step nobody asked for (for example "no protein data"), which
> silently changes the cohort. Check the drafted steps before running them, whichever model you use.

To measure a model yourself, run the benchmark in [evals/](evals/README.md):

```bash
python evals/run_eval.py --parser ai --provider openai --base-url http://localhost:11434/v1 \
    --model qwen2.5:7b --jobs 1
```

## Running the hosted AI service

For maintainers. The web app's backend ([`frontend/server.py`](frontend/server.py), deployed on AWS) is also the
hosted AI service:
- `POST /api/v1/draft` with `{"request": "..."}` returns the drafted, checked spec.
- `GET /api/v1/status` reports whether a model is configured and the caller's limits.
- [`cohort_studio/hosted.py`](cohort_studio/hosted.py) is the client.
- `POST /mcp` is the MCP server for agents. It has the same callers, tokens and limits, and refuses browser origins
  other than this app's and localhost.

The service runs the same grounded AI route on the same frozen release, so it can't be used as a general-purpose
model proxy. Every AI request goes through [`frontend/backend/limits.py`](frontend/backend/limits.py), whether it
comes from the web page, the CLI or a script. Usage is stored in the database, so a restart doesn't reset it. Every
`/api` path also has a per-address request rate and a body-size cap.

Set these in the host's environment settings, never in a committed file:

| Variable | What it does | Default |
|---|---|---|
| `OPENAI_API_KEY` | The model key (or `ANTHROPIC_API_KEY` with `COHORT_STUDIO_PROVIDER=anthropic`) | none |
| `DATABASE_URL` | Postgres for searches, the model cache and the usage counts | local SQLite |
| `COHORT_STUDIO_DAILY_BUDGET_USD` | Estimated model spend per UTC day for the whole service | `4` |
| `COHORT_STUDIO_USER_DAILY_LIMIT` / `COHORT_STUDIO_TOKEN_DAILY_LIMIT` | AI requests per person per day, without / with a token | `500` / `1000` |
| `COHORT_STUDIO_IP_DAILY_LIMIT` | AI requests per network address per day (without a token); keep it above the per-person number | `1000` |
| `COHORT_STUDIO_PER_MINUTE_LIMIT` | AI requests per person per minute | `10` |
| `COHORT_STUDIO_API_TOKENS` | Personal tokens, `name:token,name:token` (each at least 16 characters) | none |
| `COHORT_STUDIO_REQUIRE_TOKEN` | `on`: `/api/v1` and `/mcp` answer only token holders (the web app stays open) | `off` |
| `COHORT_STUDIO_AI_CALL_LIMIT` | Model calls per server run, a backstop that resets on restart | `200` |
| `COHORT_STUDIO_REQUESTS_PER_MINUTE` | Any `/api` request, per address | `240` |

To make a token, run `python -c "import secrets; print(secrets.token_urlsafe(24))"`.

The spend is an estimate from the tokens each call used and the model's list price. Also set a monthly budget on
the model provider's own dashboard as a second guard.

The per-address and per-person limits stop accidents and casual overuse, not a determined abuser: without a token, a
caller is identified by the address in the proxy's `X-Forwarded-For` header and the browser's user id, and someone
calling the server directly can fake both. The daily budget can't be faked; at worst one abuser uses up a day's AI for
everyone, who then get the built-in parser. To close the command-line and agent routes to strangers, give the team
tokens and set `COHORT_STUDIO_REQUIRE_TOKEN=on`.

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
| [`app.py`](app.py) | Streamlit app |
| [`cohort_studio/ops.py`](cohort_studio/ops.py) | The data operations, shared by the app and the exported script |
| [`cohort_studio/parse.py`](cohort_studio/parse.py) | Plain English → steps, without AI |
| [`cohort_studio/api.py`](cohort_studio/api.py) | `build_cohort`: request or definition → run, fix, verdict, exports (used by the CLI) |
| [`cohort_studio/llm.py`](cohort_studio/llm.py) | Who drafts: your model → the hosted service → the built-in parser |
| [`cohort_studio/hosted.py`](cohort_studio/hosted.py) | Client for the hosted Cohort Studio AI service |
| [`cohort_studio/mcp_server.py`](cohort_studio/mcp_server.py) | The MCP server for AI agents (stdio here; HTTP at `/mcp` via [`frontend/backend/mcp_http.py`](frontend/backend/mcp_http.py)) |
| [`cohort_studio/assistant/`](cohort_studio/assistant/) | The grounded AI route: release catalogue, prompt, step checks, questions with counts, the check against the built-in parser (`reconcile.py`) |
| [`frontend/`](frontend/) | Web app and hosted AI service: FastAPI server, single-page interface, SQLite/Postgres storage, AI limits (`backend/limits.py`) |
| [`examples/`](examples/) | A file of requests and definitions, and a script to edit and run |
| [`cohort_studio/engine.py`](cohort_studio/engine.py) | Runs the steps, records the ledger, detects and fixes inflation, measures shift |
| [`cohort_studio/verdict.py`](cohort_studio/verdict.py) | The verdict and its checks |
| [`cohort_studio/exports.py`](cohort_studio/exports.py) | `cohort_samples.csv`, reproduce script, findings.json and the rest |
| [`tests/`](tests/) | Tests on a synthetic fixture (made-up `TCGA-ZZ-…` barcodes) and the real manifests |
| [`CHALLENGE_README.md`](CHALLENGE_README.md) | The organizers' original README |

## Third-party code

pandas (BSD-3-Clause), Streamlit (Apache-2.0), Matplotlib (Matplotlib/PSF licence), jsonschema (MIT),
pytest (MIT). Web app: FastAPI (MIT), Uvicorn (BSD-3-Clause), SQLAlchemy (MIT), HTTPX (BSD-3-Clause), psycopg (LGPL-3.0; the Postgres driver, used only when `DATABASE_URL` points at Postgres). cryptography (Apache-2.0 or BSD-3-Clause; used only by the organizers' `envelopes/unseal.py`). The organizers' challenge files (`data/`, `format/`, `starter/`, `envelopes/` and others) are used as provided.
