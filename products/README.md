# Products

Pick a user. Build **one workflow that user can finish**. Featured products start with real TCGA breast cancer data. For a custom build, confirm the pack and judging scope with organizers. Build a web app, an AI agent, a visual workflow, or a tool that plugs into an existing notebook.

- **Cohort Firewall** catches what could make a result unreliable.
- **Evidence Navigator** helps someone investigate a result and decide what to do.

**The bar:** a judge can use your workflow without you narrating, and it shows **one result they can verify** and **one limitation they can inspect**.

## Featured products (full starter support)
| Track | Product | User | One-line workflow |
|---|---|---|---|
| Firewall | [Cohort Control Tower](cohort-control-tower.md) | Research engineer | Compare two data releases, catch what changed, rerun only the affected analyses |
| Firewall | [Cohort Studio](cohort-studio.md) | Researcher | Describe a cohort in plain English or on a canvas; see survivors at each step; export a rerunnable workflow |
| Navigator | [Multimodal Case Room](multimodal-case-room.md) | Research team | Open one case and see its slides, sample record and molecular evidence together, with sources linked |
| Navigator | [Evidence Copilot](evidence-copilot.md) | Scientist | Ask a question; get a bounded analysis with its sources, sample count and calculation attached |
| Navigator | [Research Claim Auditor](research-claim-auditor.md) | Reviewer | Upload a report; each claim comes back supported, contradicted or insufficient evidence |
| Navigator | [Repair Review Desk](repair-review-desk.md) | Reviewer | Receive proposed data repairs, see which findings move, and approve, reject or ask for more |

**Finale chain:** Cohort Control Tower → Repair Review Desk. One team catches a silent change in a release; another team's product receives the findings and makes a review decision. Both use the [shared format](../format/README.md).

## Pitch your own (lighter support)
These use the other [data packs](../data/README.md). They have starter data and documentation but no organizer answer key, so judges score them on the rubric alone. Tell organizers your product and pack at kickoff.

| Track | Product | Idea | Data pack |
|---|---|---|---|
| Firewall | Model Crash Lab | Stress-test a tissue classifier on altered images and find its worst failure | [PathMNIST](../data/pathmnist/README.md): use the baseline's predictions, `--size 64` or more for blur and color tests; [CAMELYON17](../data/camelyon17/README.md) for testing across hospitals (advanced) |
| Firewall | Benchmark Builder | An evaluation service for cell-line predictions that catches leaky splits | [DepMap](../data/depmap/README.md) |
| Firewall | Amendment Replay Engine | Replay fictional cases against two versions of a fictional date rule; inspect real registry snapshots separately | [ClinicalTrials.gov](../data/clinical-trials/README.md), **fictional** cases only |
| Navigator | Pathology Search and Review | Find similar tissue patches and queue uncertain predictions for review | [PathMNIST](../data/pathmnist/README.md): `review_queue.csv` from the baseline is a ready-made starting queue |
| Navigator | CT Review Viewer | Compare a model's nodule output with each radiologist's marks | [LIDC-IDRI](../data/lidc-idri/README.md) |
| Navigator | Multimodal Case Room, CPTAC edition | The featured Case Room on lung, colon or kidney cases | [CPTAC](../data/cptac/README.md) protein starter, slides and CT links |

Or pitch something else entirely: name the user, the workflow, and how it proves its track.
