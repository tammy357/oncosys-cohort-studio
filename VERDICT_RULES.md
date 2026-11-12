# How Cohort Studio decides its verdict

These rules were written before looking at any results. The thresholds are conventions, not validated cut-offs,
and they live in `cohort_studio/verdict.py`.

| Check | Pass | Warning (→ Conditional) | Fail (→ Blocked) | Can't tell (→ Insufficient Evidence) |
|---|---|---|---|---|
| Every step runs on this release | all steps ran | | a field or gene doesn't exist in this release | a needed file isn't downloaded, or a fact the step needs isn't recorded (for example sample collection dates for "earliest sample per patient") |
| The whole request was turned into steps | every word used, or unused words confirmed | | | some words weren't used and nobody confirmed they don't matter |
| Files match the frozen checksums | every raw file matches `sources.csv` | | | a file changed |
| No sample is counted twice | one row per sample at the end | | the final table has more rows than samples | |
| One sample per patient | yes (a "one sample per patient" step lists every choice in `sample_selection.csv`), or several sample types were requested | some patients contribute several samples | | |
| Sample collection days make sense (only for "earliest"/"latest") | no metastasis is dated before the same patient's primary tumor | "earliest" kept a metastasis over a primary tumor, which suggests the days don't follow surgery | | |
| Enough patients | at least 20 | | fewer than 20 | |
| The cohort still looks like where it started | no step moves submitting site, PAM50, ER, stage or age band by TVD ≥ 0.10, other than expected shifts (below) | a step (or the whole cohort) moves one by ≥ 0.10, and the move isn't expected | | |
| Filter fields are recorded | each filter field is recorded for ≥ 80% of samples | ≥ 20% missing, so those samples were dropped | | |
| Slides are of one kind | | the slide join mixes diagnostic and frozen slides | | |
| Survival endpoint suits BRCA | | overall survival is used (TCGA-CDR advises caution for BRCA; PFI preferred) | | |

**Decision rule:** Blocked if any check fails. Insufficient Evidence if a fact the cohort depends on can't be
confirmed. Conditional if any check raises a warning. Ready only when every check passes.

**Total variation distance (TVD):** half the sum of absolute differences between two category shares. 0 means the
mix is unchanged, 1 means no overlap. Fields the user filtered on are left out of that step's shift (and of the
overall shift), so a deliberate choice isn't reported as a bias. The overall shift compares the final cohort with
the population after the first sample-type step.

**Permutation p-value (shown, not yet used in the rule):** for each shift, the tool also draws the same number
of samples at random from the step's input 1,000 times and reports how often random dropout moves the mix at
least as much (`cohort_studio/stats.py`, fixed seed). The verdict still uses TVD ≥ 0.10 alone.

**Expected shifts:** some characteristics are linked to what you filter on, so moving them is the point of the filter,
not a bias. They are still reported (marked "expected", with the reason) but don't make the verdict Conditional on their
own. The links, set in `cohort_studio/data.py`: ER, PR or HER2 status ↔ PAM50 subtype (and PR or HER2 → ER status);
PAM50 → ER status; histological type → PAM50 and ER status. Shifts at the sample-type step are expected too, because that
step chooses the starting population. Every other shift, such as submitting site or age band, still raises a warning.
