# Research Claim Auditor · Evidence Navigator

**User:** a reviewer handed a report and asked "can we trust this?"

**Workflow they finish:** upload a report; an agent extracts its factual claims, checks each against the supplied data, and builds an audit queue: **supported**, **contradicted**, or **insufficient evidence**, each with the calculation or the missing evidence behind it. A person confirms each verdict.

## What you get
- The TCGA-BRCA files from `fetch.py` and the manifests
- A practice report: [`research-claim-auditor/practice_brief.md`](research-claim-auditor/practice_brief.md)

## Build (weekend MVP)
1. Upload a report (Markdown or PDF). An AI extracts each checkable claim and shows it next to the sentence it came from.
2. For each claim, run a real check against the data and record the query, numbers and sample count.
3. Sort claims into supported, contradicted or insufficient evidence. Insufficient evidence must say what data would be needed.
4. A review queue where a person accepts or overrides each verdict, keeping both.
5. Export the audit with every calculation attached, **including `audit.json` in the [shared format](../format/README.md)**: one finding per claim with `kind: "claim_check"`, `claim_text`, `claim_verdict` (`supported`, `contradicted` or `insufficient_evidence`), `correct_value` for contradicted claims, and the evidence behind it. Check it with `python format/validate.py audit.json`.

**Stretch:** highlight the exact words in the report; handle claims with ranges or approximations.

## Live demo
Run the practice report. The judge opens the calculation behind the supported claim, the correct number behind the contradicted one, and the explanation of what's missing for the claim the data can't answer.

## What judges will test
A different report with different errors. Run your tool on it live and produce `audit.json`; the organizers' checker compares your verdicts with the answer key, and judges see that comparison while they score.

**Suggested stack:** any LLM for claim extraction (no keys in the repo), pandas, Streamlit.
