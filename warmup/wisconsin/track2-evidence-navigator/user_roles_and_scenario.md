# Track 2 · Evidence Navigator — user roles and decision scenario

**Dataset:** Breast Cancer Wisconsin (Original), UCI, frozen file `data/breast-cancer-wisconsin.data`.
**Findings:** six findings records (N1–N6), shared with registered Track 2 teams at kickoff. Every view must stay consistent with these records and with the source data.

## Example users

| Role | What they need from your tool |
|---|---|
| **Data engineer** | Which rows and fields each finding touches, and how to reproduce the numbers. |
| **Researcher** | Whether the pooled figure means what it seems to, and what alternative figures exist. |
| **Data-use reviewer** | Whether the figure can be published as proposed, what must change, and who signs off. |
| **Program leader** | A plain-language answer, the main risk, and the next step, in under a minute. |

## Decision scenario

A data-use reviewer has been asked to approve publishing this sentence:

> "Across 1989–1991, 34.5% of samples in the Wisconsin collection were malignant."

Using your tool, the reviewer must choose one option and record why:

1. **Proceed:** publish the sentence as written.
2. **Fix a problem:** publish after changing how the data is prepared.
3. **Narrow the question:** publish a different or qualified figure, for example with group-level results alongside.
4. **Stop:** don't publish any figure yet.

Your tool must end with a summary recording the chosen option, the reasons, the important limitations, and who needs to review it.

**Out of scope:** classifiers, diagnosis, risk scores, or any clinical claim. This is a decision about reporting a descriptive figure.

## Expect a change at judging

Judges will introduce a new user role or a conflicting finding. Your tool should handle it without the presenter explaining.

