# Cohort Studio web app

A conversational front end for Cohort Studio. Type a cohort in plain English. The AI assistant drafts it from this
release's real fields and values, asks you when a choice would change who is in the cohort, and shows how many
samples each answer gives. Then you edit the steps, run them, fix any join that double-counts, read the verdict
and export `cohort_samples.csv`. Searches and favourites are remembered.

> Research use only. No diagnosis, treatment recommendation or clinical claim.

## Run it

From the repository root, after `python data/tcga-brca/fetch.py --no-rna`:

```bash
pip install -r requirements.txt -r frontend/requirements.txt
python frontend/server.py                  # http://127.0.0.1:8000
```

The AI assistant reads `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY`) from the environment or from the repository's
`.env` file, which git ignores. Without a key, the built-in parser and Advanced search still work.

| Setting | Default | What it does |
|---|---|---|
| `COHORT_STUDIO_MODEL` | `gpt-4.1-mini` (OpenAI), `claude-haiku-5-5` (Anthropic) | Model used for drafting |
| `COHORT_STUDIO_PROVIDER` | `openai` | `openai` (any OpenAI-compatible endpoint) or `anthropic` |
| `OPENAI_BASE_URL` | OpenAI | A local model instead, e.g. `http://localhost:11434/v1` (Ollama; no key or cost) |
| `COHORT_STUDIO_AI_CALL_LIMIT` | `200` | Paid model calls allowed per server run (protects a low-balance key) |
| `COHORT_STUDIO_DAILY_BUDGET_USD` and the other daily limits | `4`, ... | Every AI request's limits, stored in the database; see [Running the hosted AI service](../README.md#running-the-hosted-ai-service) |
| `COHORT_STUDIO_MAX_OUTPUT_TOKENS` | `1800` | Cap on each reply |
| `COHORT_STUDIO_REASONING_EFFORT` | `low` | For gpt-5 / o-series models |
| `DATABASE_URL` | `sqlite:///frontend/.data/cohort_studio.db` | Where searches and favourites are stored |
| `COHORT_STUDIO_AI` | `on` | `off` hides the AI and uses the built-in parser |
| `HOST`, `PORT` | `127.0.0.1`, `8000` | Where the server listens |

### Postgres

The same code runs on Postgres through SQLAlchemy:

```bash
pip install "psycopg[binary]>=3.1"
docker run -d --name cohort-db -e POSTGRES_USER=cohort -e POSTGRES_PASSWORD=cohort -e POSTGRES_DB=cohort_studio -p 5432:5432 postgres:16
DATABASE_URL=postgresql+psycopg://cohort:cohort@localhost:5432/cohort_studio python frontend/server.py
```

Tables are created on first start: `users`, `searches`, `favorites`, `drafts`, `llm_cache`, `versions`, `ai_usage`.

The same server is the hosted AI service for the command line and scripts (`POST /api/v1/draft`,
`GET /api/v1/status`); see [Running the hosted AI service](../README.md#running-the-hosted-ai-service).

### Deploying: pages on Vercel, API on AWS

The pages in `frontend/static/` are plain files (no build step). `frontend/static/vercel.json` serves them and
forwards every `/api/...` request to the backend, so the browser talks to one address (no CORS).

- **Vercel:** import the repository, set *Root Directory* to `frontend/static` and *Framework Preset* to "Other",
  leave the build settings empty. The backend address is the `/api/:path*` line in `vercel.json`
  (`https://api.maplestreettax.site`); change it if the backend moves.
- **AWS (backend):** one Ubuntu 24.04 EC2 server (t3.micro, 1 GB RAM, 2 GB swap) with an Elastic IP. The DNS record
  `api.maplestreettax.site` (Cloudflare, "DNS only", not proxied) points at it.
  - **App:** a systemd service `oncosys` runs
    `.venv/bin/uvicorn frontend.server:create_app --factory --host 127.0.0.1 --port 8000` from `~/oncosys-cohort-studio`.
    Install: `pip install pandas fastapi uvicorn sqlalchemy "psycopg[binary]"`, then
    `python data/tcga-brca/fetch.py --no-rna`.
  - **HTTPS:** Caddy (`/etc/caddy/Caddyfile`) gets and renews the certificate and proxies to `127.0.0.1:8000`. Its
    `trusted_proxies` setting keeps the `X-Forwarded-For` chain, so the per-address limits still see each visitor.
  - **Settings:** `~/oncosys.env` (mode 600, loaded by the service): `OPENAI_API_KEY` (secret), optionally
    `COHORT_STUDIO_MODEL`, `COHORT_STUDIO_AI_CALL_LIMIT`, the daily limits and `COHORT_STUDIO_API_TOKENS` (secret), and
    `DATABASE_URL=sqlite:////home/ubuntu/oncosys-data/cohort_studio.db`. The server's disk is permanent, so SQLite is
    enough; the AI usage counts live there. The file must use Unix line endings.
  - **Update:** nothing deploys on push. On the server run `~/update.sh` (`git pull`, re-run `fetch.py`,
    `sudo systemctl restart oncosys`). Logs: `sudo journalctl -u oncosys -f`.
- The server needs about 210 MB of memory at peak (the mutation calls are held as 1-byte integers), so a 1 GB
  instance with swap is plenty. It never sleeps, so there is no cold start.

## What it does

- **Search box with oncology autocomplete.** Suggestions come from this release's real data, each with a count
  (for example "TP53 mutated: 264", "Luminal A: 421", "stage IIIA: 156"), plus your past searches.
- **Ready-made searches** (the brief's demo and other common cohorts) and **favourites**. A favourite saved from a
  run reruns its exact steps with no model call.
- **Questions instead of guesses.** When a choice would change who is in the cohort, such as how to define
  "hormone-receptor positive", "5 years" in days or years, or a gene name that isn't in the file, the assistant asks.
  Each answer shows the number of samples it gives. A question whose answers give (nearly) the same cohort is
  resolved without asking.
- **Small corrections it makes itself, and lists.** Spelling, value case, synonyms, a field name the model
  mis-remembered: each is checked against the data and shown.
- **Honest gaps.** Anything the release doesn't contain (smoking, race, tumour grade, germline status…) is
  reported as "Not in this release", never approximated.
- **Results:** the filters behind the number (always in view, each one clickable), rows, samples and patients after
  every step, double-counting joins with examples and a one-click fix, who each step removed, the verdict with
  every check, and what a person must review.
- **Export** `cohort_samples.csv` (one button), plus `reproduce_cohort.py`, `cohort_spec.json`, `findings.json`,
  `ledger.csv`, `exclusions.csv`, `sample_selection.csv` (every "one sample per patient" choice) and `coverage.csv`
  (how much of each clinical variable is recorded) under "Other files". Paused until any double counting is fixed.
- **Calm by design.** A result leads with the number, the verdict and a row of check pills; every problem is one
  line (double counting, words not turned into steps) with its details in a tooltip, and also raises a toast.
  Shifts that follow from what you filtered on are labelled "expected" instead of being drawn as possible bias.
  The rest (all checks, limitations, things to review) is folded. Hover or focus anything with a dotted underline
  or an info icon for its explanation.
- **Advanced search:** a form with exact values and counts; no AI involved.
- **Your cohorts:** every run is saved and can be reopened, edited, favourited or deleted. The user name in the top
  bar separates people's histories (no login: this is a local tool).

## Editing a cohort

"Edit steps" (on a result, a draft, a history row, or any filter chip or flow-diagram box) opens the editor, a
panel with two ways to change the cohort. Nothing changes until you run it.

- **Edit steps.** Every step is a card with real controls: the field (grouped: receptors and subtype, tumour,
  patient, treatment, outcomes), "is one of / is not / is recorded", the release's values with their counts, number
  ranges, whole stage groups or exact sub-stages, follow-up in years or days, slide kinds, "one row per slide" or
  "once per sample", genes, data layers. Add a step anywhere, reorder, delete (with undo), or switch a step off to
  see the cohort without it. Edit as JSON if you prefer.
- **Live counts on the real data.** As you edit, each card shows samples in → out, what the cohort would be
  without that step, any double counting, and any value that can't run; the header shows the new total and the
  change. Values are checked and tidied ("negative" → "Negative"), and a filter that needs a join gets one.
- **Ask in words.** A conversation about this cohort: "drop the slide requirement", "make it ER-negative instead",
  "add TP53 mutated". The assistant receives the current steps, and returns the complete updated steps. Each reply
  lists exactly what it added, removed or changed and the new count. If it's unsure, it asks, with the count for
  each answer. "Apply to steps" lets you check or adjust before running; "Apply and run" runs it. Needs the model.
- **Versions.** Every run of a saved cohort is a version (v1, v2…) with a note of what changed. Load any version,
  run it again (it becomes the newest), compare two versions on the Compare page, or "Save as new cohort" to keep
  the original as it was.
- Shareable links: `/?q=<request>` runs a search, `/?open=<id>` reopens a saved cohort, `/?advanced=1` opens the form.

## Compare (`/compare`)

Pick any two saved cohorts or favourites (or open one from a result with "Compare with another"). The page
runs both protocols on the frozen files and shows:

- **Overlap:** an area-proportional Venn diagram, shared and unique counts, Jaccard index, shared patients.
- **What the numbers say:** plain sentences computed from the metrics, with no model involved.
- **AI insights** (optional, one model call): key differences, shared ground, which analysis each cohort suits,
  and cautions. The model sees only aggregate metrics, never patient rows. Every number in its reply is checked
  against those metrics, and any number that isn't there is listed for you to check.
- **Who is in each:** side-by-side bars for PAM50 subtype, ER status, stage, age band, sample type and
  submitting site, ranked by total variation distance.
- **Data each one has:** coverage of RNA, protein, mutation, copy number, survival, clinical and diagnostic slides.
- **Outcomes:** Kaplan–Meier curves for overall survival and the progression-free interval (one record per
  patient). A log-rank test is shown only when the cohorts share no patients. Descriptive and unadjusted.
- **How each was built:** shared steps and the steps unique to each protocol.
- **Verdicts:** both verdicts, with the checks that differ in bold.

Links: `/compare?a=s:<id>&b=s:<id>` (saved cohorts), `f:<id>` (favourites) or `v:<id>:<n>` (version n of a cohort).

## Reading aids

- **Flow diagram first.** "Where every sample went" opens on a CONSORT-style flow diagram. Click an exclusion to
  see who was excluded (by subtype, ER status and sample type, with example barcodes); click a step to edit it.
- **Funnel.** The second tab. Every bar is split by one characteristic (subtype, ER status, stage, age or sample
  type, your choice), and ribbons carry each group from one step to the next, so you see both the cohort
  narrowing and who it narrows towards. Point at a group to follow it: its share is written beside every bar.
  Under each removal, a thin bar shows who that step removed. A double-counting join shows its extra rows as a
  dashed outline. The colours were checked for colour-blind separation, lightness and contrast in both themes;
  ordered characteristics (stage, age) use one hue from light to dark, and "not recorded" is hatched grey.
- **Technical terms in bold.** Terms such as PAM50, IHC, RPPA, MC3, TVD, PFI, join and diagnostic (FFPE) are
  bold wherever they appear in explanations; hover one for a one-line definition.
- **Advanced search** is a sectioned form with tap-to-select options showing real counts, and a live count of
  samples and patients that updates as you choose.

## Design

"Specimen": paper and ink, with the two colours of an H&E-stained slide (hematoxylin indigo, eosin pink).
Inter for everything people read; JetBrains Mono only for sample IDs and code.

- **The background is the data.** A three.js field draws one dot for each of the release's 1,247 samples,
  grouped by PAM50 subtype inside a microscope-style reticle. The layout is illustrative and says so in its legend.
  Point at a legend entry to isolate a subtype. When a cohort runs, its samples stay lit and the rest drift back
  and fade.
- **Results read like a paper's flow diagram.** Every step is a box, every exclusion branches off with its count
  and reason, a double-counting join is outlined, and the final cohort is set in ink.
- **The editor is a pipeline.** Steps sit on a rail from "All samples" to "Cohort"; new and edited steps are
  marked, switched-off steps are struck through, and the counts update in place.
- **The ready-made cohorts** each carry their real attrition curve (for example 1,247 → 731).
- **Motion** uses GSAP: a staged headline reveal, counters, flow diagrams drawn step by step, curves drawn on
  scroll. All motion stops when the system asks for reduced motion.
- Light and dark themes. Links: `?q=` runs a search, `?prefill=` fills the box, `?open=<id>` reopens a cohort,
  `?advanced=1` opens the form, `?theme=dark`.

### Front-end libraries (loaded from jsDelivr / Google Fonts at pinned versions)

| Library | Version | Licence | Used for |
|---|---|---|---|
| three.js | 0.160.0 | MIT | The background sample field |
| GSAP + ScrollTrigger | 3.12.5 | GreenSock standard licence (no charge) | Motion |
| Lucide | 0.460.0 | ISC | Icons |
| Inter, JetBrains Mono | Google Fonts | SIL Open Font Licence | Type |

Each one is optional. Offline, the app works the same with system fonts, no motion and no background field.

## How the AI stays grounded

The assistant lives in `cohort_studio/assistant/`. The built-in rule parser (`cohort_studio/parse.py`) is separate
and unchanged.

1. `catalog.py` profiles the release: sample types, data layers, slide kinds, every usable clinical column with its
   real values and counts, survival fields and the gene list. It also lists what the release does not contain.
2. `prompt.py` sends those facts (the same every time, so providers can cache them) plus, for this request only,
   the values of the columns it probably needs and a check of every gene it names.
3. `verify.py` checks every step the model writes against the data. A clear fix is made and recorded. A fix with
   several plausible answers becomes a question. Anything impossible becomes "unsupported".
4. `draft.py` runs every answer option on the data to show its count. It flags any word that changes meaning
   (not, without, or, per…) that no step accounts for, and turns your answers into the runnable spec.

The model never sees patient-level data and never produces a number: every count comes from the engine.

## Tests

```bash
python -m pytest tests frontend/tests -q
```

The tests use a synthetic release and a scripted stand-in model, so they need no download, key or network.
