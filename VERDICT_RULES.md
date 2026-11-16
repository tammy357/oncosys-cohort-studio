# How Cohort Studio decides its verdict

The original rules were written before looking at any results. The thresholds are conventions, not validated
cut-offs, and they live in `cohort_studio/verdict.py`. Four changes were made after the event, in response to the
judges' feedback (November 2026); they are marked **(changed after the event)** below, so it is clear they were
not set in advance.

| Check | Pass | Warning (→ Conditional) | Fail (→ Blocked) | Can't tell (→ Insufficient Evidence) |
|---|---|---|---|---|
| Every step runs on this release | all steps ran | | a field or gene doesn't exist in this release | a needed file isn't downloaded, or a fact the step needs isn't recorded (for example sample collection dates for "earliest sample per patient") |
| The whole request was turned into steps | every word used, or unused words confirmed by a named person | | | some words weren't used and nobody confirmed they don't matter |
| Every open reading was confirmed | no open readings, or a named person confirmed them | | | the draft settled a reading nobody chose |
| Files match the frozen checksums | every raw file matches `sources.csv` | | | a file changed |
| No sample is counted twice | one row per sample at the end | | the final table has more rows than samples | |
| One sample per patient | yes (a "one sample per patient" step lists every choice in `sample_selection.csv`), or several sample types were requested | some patients contribute several samples | | |
| Sample collection days make sense (only for "earliest"/"latest") | no metastasis is dated before the same patient's primary tumor | "earliest" kept a metastasis over a primary tumor, which suggests the days don't follow surgery | | |
| Enough patients | at least 20 | | fewer than 20 | |
| The cohort still looks like where it started | no step moves submitting site, PAM50, ER, stage or age band by TVD ≥ 0.10 beyond chance, other than expected shifts (below) | a step (or the whole cohort) moves one by ≥ 0.10, random dropout of the same size does that less than 5% of the time (p < 0.05), and the move isn't expected **(changed after the event: the p-value was shown but not used)** | | |
| Filter fields are recorded | each filter field is recorded for ≥ 80% of samples | ≥ 20% missing, so those samples were dropped | | |
| Slides are of one kind | | the slide join mixes diagnostic and frozen slides | | |
| Survival endpoint suits breast cancer | | overall survival is used or named as the outcome (TCGA-CDR advises caution for BRCA; PFI preferred) | | |
| The outcome you named is recorded (only when an outcome is named) **(new after the event)** | recorded for ≥ 80% of cohort samples and, for a survival endpoint, at least 10 events | under 80% recorded, or fewer than 10 events | | the outcome's file isn't available, or no such field exists |

**Decision rule:** Blocked if any check fails. Insufficient Evidence if a fact the cohort depends on can't be
confirmed. Conditional if any check raises a warning. Ready only when every check passes.

**Total variation distance (TVD):** half the sum of absolute differences between two category shares. 0 means the
mix is unchanged, 1 means no overlap. Fields the user filtered on are left out of that step's shift (and of the
overall shift), so a deliberate choice isn't reported as a bias. The overall shift compares the final cohort with
the population after the first sample-type step.

**Permutation p-value (part of the rule since the event):** for each shift, the tool draws the same number of
samples at random from the step's input 1,000 times and records how often random dropout moves the mix at least as
much (`cohort_studio/stats.py`, fixed seed). A shift of TVD ≥ 0.10 is flagged only when p < 0.05. Large shifts that
random dropout often produces are still listed in the check's detail, marked "within chance". When the test can't
run (a join added samples, or the characteristic has missing values), TVD ≥ 0.10 alone decides, as before. With six
characteristics tested at each step, some flags at p < 0.05 will still be chance; the threshold is not corrected for
multiple tests.

**Intended use (new after the event):** name the outcome the cohort is for (`--outcome pfi`, or `outcome=` in
`build_cohort`) and the verdict adds the outcome check above. The same cohort can then be Ready for one analysis and
Conditional for another. Without an outcome, the limitations say that outcome coverage wasn't checked.

**Sign-off (changed after the event):** confirming unused words or open readings needs a person's name
(`--accept-unparsed --reviewer NAME`, optionally `--reviewer-role` and `--review-note`). The name, date, role and
note are saved in `cohort_spec.json` and quoted in the verdict.

**Expected shifts:** some characteristics are linked to what you filter on, so moving them is the point of the filter,
not a bias. They are still reported (marked "expected", with the reason) but don't make the verdict Conditional on their
own. The links, set in `cohort_studio/data.py`: ER, PR or HER2 status ↔ PAM50 subtype (and PR or HER2 → ER status);
PAM50 → ER status; histological type → PAM50 and ER status. Shifts at the sample-type step are expected too, because that
step chooses the starting population. Every other shift, such as submitting site or age band, still raises a warning.
