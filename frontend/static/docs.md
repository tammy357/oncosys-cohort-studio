# Cohort Studio docs

Cohort Studio turns a plain-English request into a TCGA-BRCA cohort you can defend: every step shows its count, a join
that counts a sample twice is caught and fixed, and every cohort gets a verdict with the checks behind it. Every number
comes from the cohort engine running on frozen files; the AI model only drafts steps and never produces a count.

> Research use only. No diagnosis, treatment recommendation or clinical claim.

**For AI agents:** this page is plain Markdown at [`/docs.md`](/docs.md), with an index at [`/llms.txt`](/llms.txt). The
[For AI agents](#for-ai-agents) section says how to guide someone through the app, the links that open each feature,
and the API and MCP tools.

## Getting started

The app has three pages, each in the top bar:

| Page | Address | What it is for |
|---|---|---|
| **Build** | `/` | Describe a cohort in words, answer any question it asks, read the result, fix problems, export |
| **Canvas** | `/canvas` | The same cohort as a visual pipeline you can drag, drop and inspect, with the SQL beside it |
| **Compare** | `/compare` | Any two cohorts side by side: who they share and how they differ |

New here? Each page offers a one-minute tour the first time you open it, and **Help (?)** in the top bar has the tours,
five practice tasks and these docs at any time. See [Tours and practice](#tours-and-practice).

## Build

### Describe a cohort

Type the cohort you want in the search box, for example *primary tumours with RNA and protein data and a diagnostic
slide*. Suggestions come from the release's real data, each with a count ("TP53 mutated: 264", "Luminal A: 421").

With the **AI assistant** on, the model drafts the steps from the release's real fields and values, and the built-in
rule parser drafts the same request as a second opinion. If both select the same samples, that's it; if they differ,
the app compares the two drafts and says which it kept and why. With the assistant off (or no model available), the
built-in parser drafts on its own. **Advanced search** is a form with exact values and counts and no AI at all.

The draft is shown as steps. Nothing runs until you press **Run protocol**.

### Questions instead of guesses

When a request could mean two things, and the choice changes who is in the cohort, the app asks instead of guessing.
Each answer shows how many samples it would keep. Typical questions: how to define "hormone-receptor positive", what
age counts as "older", whether "5 years" means overall survival or progression-free time, or which gene you meant.
A question whose answers give (nearly) the same cohort is settled without asking. You can also answer in your own words.

Small corrections it can make safely (spelling, value case, a synonym, a mis-remembered field name) are made and listed.
Anything the release doesn't contain is reported as **Not in this release**, never approximated.

![Video: It asks before it guesses](/static/tour/build-ask.mp4)

### Reading a result

A result leads with the **number of samples**, the **verdict** and a row of **check pills**. Below them:

- **Filters**: the steps behind the number, always in view. Click one to change that step.
- **Where every sample went**: a CONSORT-style flow diagram. Every step is a box with its count; every exclusion
  branches off with its count and reason. Click an exclusion to see who was removed.
- **Funnel**: the same steps as bars split by subtype, ER status, stage, age or sample type, so you see who the
  cohort narrows towards.
- **Who the steps removed**: shifts in the cohort's mix. A shift that follows from what you filtered on is labelled
  *expected* rather than drawn as possible bias.

Hover anything with a dotted underline or an info icon for its explanation.

### Double counting

A **join** adds a table to the cohort. Some tables have several rows per sample (a sample can have several pathology
slides), so joining one row per slide makes the same sample appear more than once. Counts inflate and statistics drift.

Every step shows rows, samples and patients. When rows exceed samples, the step is flagged in pink with how many rows
are counted twice and example barcodes. **Fix it** switches the join to *once per sample* (or adds a de-duplication
step), so every sample counts once. Export stays paused until any double count is fixed.

![Video: Double counts, caught](/static/tour/build-fix.mp4)

### Verdicts and checks

Every cohort gets one of four verdicts:

| Verdict | Meaning |
|---|---|
| **Ready** | Every check passes |
| **Conditional** | A check raised a warning (for example a step shifted the cohort's subtype mix, or several samples per patient) |
| **Blocked** | A check failed (for example a sample is counted twice, a field doesn't exist, or fewer than 20 patients) |
| **Insufficient evidence** | A fact the cohort depends on can't be confirmed |

Hover a check pill for its detail. **All checks** lists every check; **Before anyone uses it** lists what a person
must review; **Limitations** lists what the data can't tell you. The rules were written before looking at any
results (`VERDICT_RULES.md` in the repository).

![Video: A verdict with evidence](/static/tour/build-verdict.mp4)

### Editing steps and versions

**Edit steps** (on a result, a draft, a history row, a filter chip or a flow-diagram box) opens the editor:

- **Edit steps**: every step is a card with real controls (fields grouped by topic, values with their counts, number
  ranges, stage groups, follow-up in years or days, slide kinds, genes, data layers). Add, reorder, delete or switch
  a step off to see the cohort without it. Live counts update as you edit. Edit as JSON if you prefer.
- **Change in words**: "drop the slide requirement", "make it ER-negative instead". The reply lists exactly what it
  added, removed or changed, and the new count. Needs the AI model.
- **Versions**: every run of a saved cohort is a version (v1, v2…) with a note of what changed. Load, rerun, compare
  two versions, or save as a new cohort.

### Export

**Download cohort_samples.csv** gives one row per sample (a `sample_id` column with the 15-character TCGA barcode, no
duplicates). **Other files** has:

| File | What it is |
|---|---|
| `reproduce_cohort.py` | Rebuilds exactly this cohort from the frozen files |
| `cohort_spec.json` | The cohort definition; rerun it anywhere |
| `ledger.csv`, `exclusions.csv` | Every step's rows, samples and patients; every removed sample and why |
| `sample_selection.csv` | Every "one sample per patient" choice |
| `coverage.csv` | How much of each clinical variable is recorded for the cohort |
| `findings.json` | The verdict and its checks |

**Open in canvas** opens the same cohort on the canvas; **Compare with another** opens it on the Compare page.

![Video: Take it anywhere](/static/tour/build-take.mp4)

### Your cohorts and favourites

Every run is saved under **Your cohorts** and can be reopened, edited, favourited, compared or deleted. A favourite
reruns its exact steps with no model call. The name in the top bar keeps each person's history separate (there is no
login: anyone can type any name).

## Canvas

The canvas shows a cohort as a pipeline you can build by hand. It fills the window; a small toolbar at the bottom opens
everything else, and **Esc** closes whatever is open.

### Reading the canvas

- Each **card** is one step with the engine's own count: samples, patients, and rows when a step adds extra rows.
- **Wires** are drawn to scale: the ink core is the samples flowing on, a pink halo the extra rows a join added, and a
  thin branch under a card the samples it removed. The number on a wire is the samples flowing on.
- A card shows at most one status line: an error, extra rows, an automatic join, a shift in the cohort's mix, or
  missing values.
- The **status pill** in the top bar shows the cohort's samples, patients, verdict and how many steps need a look.
  Click it for the verdict and every check.

![Video: Your cohort as a pipeline](/static/tour/canvas-read.mp4)

### Adding steps

Press **Add** (or the **A** key) for the block library: sample types, tables, data layers, every clinical and survival
field with its values, genes, rules and templates. **Drag a block onto a wire** to insert it there, or click it to add
it at the end. Click the number on a wire to add a step at that point. Search finds fields, values and gene symbols;
a gene the release doesn't have suggests the closest names.

If a block needs another table (a clinical field needs the clinical records), the join is added for you.

![Video: Drop in any step](/static/tour/canvas-add.mp4)

### Fixing a double count

A join that adds rows turns pink, with a pill underneath: "*N* extra rows · Fix". Click the card and **See why** opens
the X-ray on a real patient, showing which slides became rows. **Fix** keeps each sample once.

![Video: See why, then fix it](/static/tour/canvas-fix.mp4)

### Grain lanes

**Grain** is what one row stands for after a step: one per patient, one per sample, or several rows per sample.
**Grain lanes** in the toolbar places each card in the lane of its grain, so a join that double counts visibly drops a
lane, and its fix brings it back. **Flow** returns to the single row.

![Video: See what a row means](/static/tour/canvas-lanes.mp4)

### The inspector

Click a card to open the inspector on the right: its settings first (chips with counts, a visual AND / OR / NOT builder
for combined conditions), then what it did: samples in and out, the cohort without it, who it removed, shifts in the mix
and missing values. From here you can switch a step off, duplicate, move or delete it.

### Code and verified SQL

**Code** shows the cohort as **SQL** (DuckDB, one CTE per step, the selected step's lines highlighted), the **Python**
reproduce script, and the raw **JSON** steps. The server runs the SQL in DuckDB on the frozen files and compares every
step's rows, samples and patients, and the final sample IDs, with the engine: **Verified** appears only when all of
them match. Copy or download any of the three; edit the JSON to replace the steps.

![Video: Code you can check](/static/tour/canvas-code.mp4)

### Slides and the X-ray

**Slides** opens a sheet of the cohort's real pathology slides (thumbnails from NCI Imaging Data Commons, CC BY 3.0).
Filter by kind; click a slide to open it in the IDC viewer. The **X-ray** shows one patient as a tree (patient →
samples → slides) and which slides become rows in a join.

### Ask in words

**Ask** (or **/**) describes a new cohort in words, or with **Change this**, changes the current one ("drop the slide
requirement"). If a choice changes who is in, it asks first, with the count for each answer. Every card can be edited
afterwards, and Ctrl+Z undoes the change.

### Saving and exporting

**Save** keeps the cohort in your history without leaving the canvas (each save of the same cohort is a new version).
**Save & open** in the Cohort panel opens it on the Build page. **Export** saves and downloads `cohort_samples.csv`,
and is held back while a join double counts.

### Keyboard shortcuts

| Key | Does |
|---|---|
| A | Open the block library |
| / | Describe a cohort in words |
| ← → | Move between cards |
| Alt + ← → | Move the selected card |
| Delete | Remove the selected step |
| Ctrl + Z, Ctrl + Shift + Z | Undo, redo |
| F | Fit the whole pipeline in view |
| + / − | Zoom (Ctrl + wheel also zooms; the wheel pans) |
| Esc | Close what's open |

## Compare

Pick any two saved cohorts, favourites or versions (or use **Compare with another** on a result). Both are rerun on the
same frozen files, then the page shows:

- **Overlap**: an area-proportional diagram, shared and unique counts, the Jaccard index and shared patients.
- **What the numbers say**: plain sentences computed from the metrics, with no model involved.
- **AI insights** (optional, one model call): the model sees only aggregate metrics, and every number in its reply is
  checked against them.
- **Who is in each**: PAM50 subtype, ER status, stage, age band, sample type and submitting site, ranked by how
  different the two mixes are.
- **Data each one has**, **Outcomes** (Kaplan–Meier curves, one record per patient; descriptive and unadjusted),
  **How each was built** and both **Verdicts**.

![Video: Any two cohorts](/static/tour/compare-pick.mp4)

## Tours and practice

- **Tours**: each page offers a one-minute tour the first time you open it in a browser, as a small card in the
  corner. Take it, or choose *Not now*; it never comes back on its own. **Help (?)** → *Take the tour* replays it.
  Each step has a short video with a voice-over; mute it, turn on captions, or skip at any time.
- **Practice**: five real tasks that tick themselves off when you do them: answer a clarifying question, fix a double
  count, drag a block onto a wire, check the SQL is verified, and compare two cohorts. **Show me** plays how; **Do it**
  sets the task up (loading the demo on the canvas is undoable with Ctrl+Z). Progress stays in your browser.

Links: `/?tour=1`, `/canvas?tour=1` and `/compare?tour=1` open a page's tour.

## For AI agents

How to help someone with Cohort Studio:

1. **Never invent counts.** Every number must come from the app, the API or the MCP tools. If the user asks "how many",
   build the cohort and read the engine's answer.
2. **Point at the feature that answers the question.** Ambiguous request → the clarifying question
   ([Questions instead of guesses](#questions-instead-of-guesses)). "Why are my numbers inflated?" → a join that
   double counts ([Double counting](#double-counting), [Fixing a double count](#fixing-a-double-count)). "Can I trust
   this cohort?" → the verdict and its checks ([Verdicts and checks](#verdicts-and-checks)). "Give me the code" →
   `reproduce_cohort.py` or the canvas's verified SQL ([Code and verified SQL](#code-and-verified-sql)).
3. **Say what the release can't answer.** These are not in this release: smoking or tobacco history, alcohol use,
   family history of cancer, germline (inherited) variant status (only somatic MC3 mutation calls exist), race or
   ethnicity, body mass index or weight, tumour grade, Ki-67, named drugs or regimens (only yes/no therapy flags),
   treatment response, socioeconomic data.
4. **Use the links below** to open the exact screen the user needs.

### Deep links

| Link | Opens |
|---|---|
| `/?q=<request>` | Build, running that request |
| `/?prefill=<request>` | Build, with the request typed but not run |
| `/?open=<id>` | A saved cohort on Build |
| `/?advanced=1` | Advanced search |
| `/canvas?open=<id>` | A saved cohort on the canvas |
| `/canvas?template=<n>` | A ready-made cohort on the canvas (0 is the double-count demo) |
| `/canvas?q=<request>` | The canvas, drawing that request |
| `/compare?a=s:<id>&b=s:<id>` | Two saved cohorts compared (`f:<id>` for a favourite, `v:<id>:<n>` for version n) |
| `?theme=dark` | Any page in the dark theme |
| `?tour=1` | A page's tour |

### API

The web app's backend (all counts from the engine). Send JSON; `x-user: <name>` scopes saved cohorts.

| Endpoint | What it does |
|---|---|
| `POST /api/draft` `{query, use_ai}` | Draft steps from words (may return clarifying questions) |
| `POST /api/drafts/<id>/resolve` `{answers}` | Answer a draft's questions |
| `POST /api/run` `{spec, query, mode}` | Run steps; returns counts, the verdict and a saved cohort id |
| `POST /api/preview` | Counts for steps without saving |
| `GET /api/searches`, `GET /api/searches/<id>` | Saved cohorts |
| `POST /api/searches/<id>/fix` `{step_index}` | Fix a double-counting step |
| `GET /api/searches/<id>/files/<name>` | An export (`cohort_samples.csv`, `reproduce_cohort.py`, …) |
| `GET /api/compare?a=…&b=…` | Compare two cohorts |
| `GET /api/canvas/catalog` | Tables, layers, sample types, genes, templates, what isn't in the release |
| `POST /api/canvas/evaluate` `{steps}` | Every step's counts, grain, fixes and the verdict |
| `POST /api/canvas/code` `{steps, verify}` | SQL, Python and JSON; with `verify`, the SQL checked against the engine |
| `GET /api/canvas/genes?q=<symbol>` | Whether a gene is in the mutation file, and the closest names |
| `GET /api/canvas/patients/<id>` | One patient's samples, slides and data layers |
| `POST /api/v1/draft`, `GET /api/v1/status` | The hosted AI service used by the command line |

Interactive API reference: `/api/docs`.

### MCP

Cohort Studio is an MCP server, so an agent can build cohorts as tools. Hosted (streamable HTTP):
`https://api.maplestreettax.site/mcp`. Local: `python -m cohort_studio.mcp_server` from a clone.

| Tool | What it does |
|---|---|
| `describe_release` | Sample types, data layers, slide kinds, every field's exact values and counts, and the step format |
| `draft_cohort` | Plain English → an editable cohort definition, not run |
| `build_cohort` | A request or a definition → every step's counts, any inflating join fixed, the verdict and the sample IDs |
| `get_cohort_file` | A definition → `cohort_samples.csv`, `reproduce_cohort.py` or any other export |

The command line (`python -m cohort_studio "<request>"`) and Python (`cohort_studio.api.build_cohort`) do the same
from a clone of the repository.

## Data and limits

- **Release:** TCGA-BRCA, frozen: 1,247 samples from 1,101 patients (1,101 primary tumours, 139 solid tissue normals,
  7 metastatic). Data layers: clinical (1,101 samples), survival (1,097), RNA (1,097), copy number (1,080), mutation
  (791), protein (742). 3,111 pathology slides (diagnostic and frozen sections).
- **Sources:** TCGA Research Network via the UCSC Xena hub; curated survival from the TCGA Clinical Data Resource;
  slides from NCI Imaging Data Commons (CC BY 3.0).
- **Privacy:** the language model never sees patient-level data. It receives the request and the release's field
  catalogue; compare insights see only aggregate metrics.
- **Not in this release:** see [For AI agents](#for-ai-agents), point 3.

## Glossary

| Term | Meaning |
|---|---|
| **Cohort** | The set of samples a study uses |
| **Step** | One operation in a cohort: keep a sample type, require a data layer, filter, join, one per patient… |
| **Join** | Adding a table (slides, clinical, survival, normals) to the cohort |
| **Double count** (inflation) | A join that makes a sample appear on more than one row |
| **Grain** | What one row stands for: a patient, a sample, or several rows per sample |
| **Verdict** | Ready, Conditional, Blocked or Insufficient evidence, from fixed rules |
| **TVD** | Total variation distance: how much a group mix moved (0 unchanged, 1 no overlap); 0.10 or more raises a warning |
| **PAM50** | The intrinsic subtype (Luminal A, Luminal B, HER2-enriched, Basal-like, Normal-like) |
| **RPPA** | Reverse-phase protein array (the protein layer) |
| **MC3** | The TCGA somatic mutation calls |
| **FFPE** | Formalin-fixed, paraffin-embedded: diagnostic slides (frozen sections are the other kind) |
| **OS, PFI** | Overall survival; progression-free interval (preferred for BRCA) |
| **Jaccard index** | Shared samples ÷ samples in either cohort |
