# Sealed envelopes: how you get the unseen input on Saturday

On Saturday, judges run your tool on something you haven't seen. You won't download anything in the room. The inputs are already in this folder, **sealed**. At the start of your Q&A, a judge gives you an **envelope number** and a **four-word code**, and you open it:

```
python envelopes/unseal.py 07 amber falcon river quartz
```

```
Opening envelope E07

  ✓ Release A matches the published fingerprints
  ✓ Envelope E07 matches its published fingerprint
  ✓ Envelope E07 opened
  ✓ All 5 release files match their fingerprints

  Done in 14.2 s. Everything is in:  envelopes/opened/E07/
  New release for your tool:   envelopes/opened/E07/release/
```

Unsealing takes about 10 to 20 seconds, mostly checking fingerprints and rebuilding files. Then run your tool on what's inside. Every team gets its own envelope, and a code opens only that envelope.

## What's inside an envelope
| Path | For | What it is |
|---|---|---|
| `release/` | Cohort Control Tower | A **new TCGA-BRCA release**: the same 5 file names and format as release A (`clinical.tsv`, `survival.tsv`, `mutation_gene_level.tsv.gz`, `copy_number_gistic.tsv.gz`, `protein_rppa.tsv.gz`). **No RNA file**, and no `CHANGES.json`: finding the changes is your tool's job. |
| `repair_desk_input.json` | Repair Review Desk | Findings to review, in the shared format |
| `claim_auditor_report.md` | Research Claim Auditor | A short report whose claims you check |
| `cohort_studio_request.md` | Cohort Studio | The cohort to build |
| `evidence_copilot_questions.md` | Evidence Copilot | The questions to answer |
| `case_room_case.txt` | Multimodal Case Room | The case to open |

Use the file for your product. Pitch-your-own teams: judges will tell you which file to use, if any.

## Get ready this week
1. **Pull the latest repo** (`git pull`). The sealed envelopes are in `envelopes/sealed/`: all of them together are under 1 MB.
2. **Install the requirements** (`pip install -r requirements.txt`). Unsealing uses `pandas` and `cryptography`, which Colab already has.
3. **Fetch release A** (`python data/tcga-brca/fetch.py`). The envelope stores only what changed and rebuilds the rest from your release A, after checking its fingerprints.
4. **Rehearse with the practice envelope.** Its code is public:
   ```
   python envelopes/unseal.py practice orange meadow pilot lantern
   ```
   It contains practice release B, without RNA. Point your tool at `envelopes/opened/practice/release/` and time the whole run: unseal, load, write your output. Unsealing takes 10 to 20 seconds, so aim for your tool to finish in under a minute after that.
5. **Make your tool take a folder path** as input, so it works with any envelope.

## If something goes wrong on the day
- **"That code doesn't open envelope…":** check the envelope number and the four words. Spaces or hyphens between words both work, and capitals don't matter.
- **"Release A isn't here" or a fingerprint error:** run `python data/tcga-brca/fetch.py` again. Do this before Saturday, not during your Q&A.
- **Working in Colab:** run these in a cell first, then use the same `unseal.py` command with `!` in front:
  ```
  !git clone https://github.com/disleveK/oncosys-hackathon-challenge
  %cd oncosys-hackathon-challenge
  !python data/tcga-brca/fetch.py
  ```

`opened/` is ignored by git, so opened envelopes are never committed.
