# Repair Review Desk · Evidence Navigator

**User:** a reviewer who must approve or reject changes to research data.

**Workflow they finish:** receive a Firewall tool's findings and proposed repairs, see before and after which analyses move, and approve, reject or request more evidence for each repair, keeping the original analysis and the reason for every decision.

## What you get
- The organizers' reference Control Tower output: [`format/examples/control-tower-practice-B.json`](../format/examples/control-tower-practice-B.json)
- Release A (`fetch.py`) and practice release B (`make_release.py`), so you can recompute anything a repair claims
- `data/tcga-brca/reference_analyses.py` to rerun analyses A1 and A2
- The [shared format](../format/README.md). **Required:** you read findings and repairs, and write `decisions`.

## Build (weekend MVP)
1. Import a findings file and validate it (`format/validate.py`).
2. For each proposed repair: what it changes, which findings it addresses, and the before and after results of the affected analyses. **Recompute them yourself;** don't just trust the file.
3. A decision for each repair: approve, reject, or request more evidence, with a reason and the reviewer's role. Unsupported repairs should be easy to spot.
4. Export the decisions back into the same file, with the original analysis preserved.

**Stretch:** a diff view of the data before and after a repair; a history of decisions.

## Live demo
Import the reference output. Review its repairs, reject one that isn't supported by the findings, and export a decision record that keeps the original analysis and the reason for the rejection.

## What judges will test
A findings file you haven't seen, including at least one unsupported repair. Import it live and export your `decisions`; the organizers' checker compares them with the answer key, and judges see that comparison while they score. At the finale, you also import a **different team's** Control Tower output.

**Suggested stack:** Streamlit or a small web app, pandas, the validator.
