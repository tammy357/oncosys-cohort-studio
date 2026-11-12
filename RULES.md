# Rules

## What you can build
- Anything that serves one user and one workflow in your track: a web app, AI agent, visual workflow, command-line tool, or notebook plug-in.
- One of the six featured products, a combination of two, or your own pitch.
- **Research models are allowed** (for example a classifier on public image patches), judged on robustness and honest reporting of failures. Evaluate on held-out data split by patient, slide or cell line, and include a short model card: data used, split, where it fails.

## Data
- **Judged work uses the event's frozen TCGA-BRCA release:** the files `data/tcga-brca/fetch.py` downloads, verified against the checksums in `data/tcga-brca/sources.csv`, plus the manifests in that folder.
- **Pitch-your-own products** may add public, permissively licensed data (for example PathMNIST). Tell the organizers which source at kickoff, and list it in your README.
- **This data is real, de-identified and patient-derived.** It has no names or contact details, but it came from real people.
  - Don't attempt to re-identify anyone or link records to any outside source about individuals.
  - Don't add controlled-access data, protected health information, participant-supplied patient records, or anything confidential from an employer.
- **Trial-related products use fictional cases only.**

## What's out of scope
- No diagnosis, treatment recommendation, patient risk score, or claim of clinical validity, regulatory compliance or production readiness.
- A tissue-class label on an image patch or a model score is a research output, not a diagnosis. Say so in your interface.

## Your repository
- No secrets: no API keys, tokens or private links. AI features run with a local model or a key the user supplies.
- No raw TCGA files committed (they're fetched); small derived outputs are fine.
- List third-party code and its license.

## Judging
Each track has its own 7-criterion, 100-point rubric (see the challenge package). Judges use your tool live and give it an input you haven't seen. A finished workflow beats an ambitious one that doesn't run. "This data can't answer the question, and here's why" is a valid, winning result.
