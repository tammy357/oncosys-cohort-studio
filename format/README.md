# Shared findings format (v0.2)

One small JSON file that one team's tool writes and another team's tool reads. It's how a Cohort Firewall product hands its results to an Evidence Navigator product.

**Required** for Cohort Control Tower (writes findings), Repair Review Desk (reads them and adds decisions) and Research Claim Auditor (writes one `claim_check` finding per claim). **Optional** for every other product, but using it makes your tool work with others.

## Check your file
```bash
pip install jsonschema
```
```bash
python format/validate.py my_findings.json
```
The validator checks the structure, and that every repair and decision points at a finding or repair that exists.

## What goes in it
| Part | Who writes it | What it holds |
|---|---|---|
| `producer`, `data`, `question` | Every tool | Your team and product; the release(s) and file checksums the findings are about; the question asked |
| `verdict` | Optional | Ready / Conditional / Blocked / Insufficient Evidence, or Proceed / Fix a problem / Narrow the question / Stop |
| `findings` | Firewall tools | What's wrong, how sure you are, the evidence, and which analyses it affects **and doesn't** affect |
| `analyses` | Firewall tools | Before and after results of each analysis you reran |
| `repairs` | Firewall tools | Proposed fixes, each tied to the findings it resolves |
| `decisions` | Navigator tools | Approve, reject or request more evidence for each repair, with the reason and the reviewer's role |
| `claim_text`, `claim_verdict`, `correct_value` (in a finding) | Claim Auditor | The claim, its verdict (supported, contradicted, insufficient evidence) and, if contradicted, what the data shows |

**Evidence pointers** (`kind` + `id`) can name a sample, patient, gene, protein, slide, file, release, analysis, claim or row. Add `release`, `file` or a viewer `url` so a reviewer can find it.

## Reference output
[`examples/control-tower-practice-B.json`](examples/control-tower-practice-B.json) is the organizers' Control Tower output for the public practice release. Repair Review Desk teams can build against it before a Control Tower team's output exists.

**Spoiler:** it lists what changed in practice-B. Control Tower teams should run their own tool before reading it.
