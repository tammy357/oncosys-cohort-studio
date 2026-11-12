"""Ready / Conditional / Blocked / Insufficient Evidence, with the checks behind it.

The thresholds are conventions chosen before looking at results (see VERDICT_RULES.md).
They are not validated cut-offs, and the app says so.
"""
from dataclasses import asdict, dataclass, field

from .spec import describe_step

MIN_PATIENTS = 20          # fewer patients than this: Blocked
SHIFT_WARN = 0.10          # total variation distance at or above this: Conditional
MISSING_WARN = 0.20        # a filter field missing for at least this share of samples: Conditional

DECISION_RULE = ("Blocked if any check fails. Insufficient Evidence if a fact the cohort depends on can't be "
                 "confirmed. Conditional if any check raises a warning. Ready only when every check passes.")


@dataclass
class Check:
    name: str
    status: str            # pass, warn, fail, unknown
    detail: str
    review: str = ""       # what a person must look at, if anything
    fields: list = field(default_factory=list)


@dataclass
class Verdict:
    verdict: str
    reason: str
    checks: list
    human_review: list
    decision_rule: str = DECISION_RULE
    limitations: list = field(default_factory=list)

    def to_dict(self):
        d = asdict(self)
        d["checks"] = [asdict(c) for c in self.checks]
        return d


def _is_unknown(message):
    m = message.lower()
    return "isn't available" in m or "checksum" in m or "fetch.py" in m or "aren't recorded" in m


def evaluate(result, store):
    spec = result.spec
    steps = spec.get("steps", [])
    checks = []

    # 1. Did every step run on this release?
    errors = [s for s in result.steps if s.error]
    if errors:
        first = errors[0]
        unknown = _is_unknown(first.error)
        checks.append(Check("Every step runs on this release", "unknown" if unknown else "fail",
                            f"Step {first.index + 1} ({first.label}): {first.error}",
                            "Fetch the data or edit the step, then run again."))
    else:
        checks.append(Check("Every step runs on this release", "pass", f"All {len(steps)} steps ran."))

    # 2. Was the whole request used?
    unparsed = spec.get("unparsed") or []
    if unparsed and not spec.get("unparsed_confirmed"):
        checks.append(Check("The whole request was turned into steps", "unknown",
                            f"These words were not used: {', '.join(unparsed)}.",
                            "Add a step for them, or confirm they don't change the cohort."))
    else:
        detail = "Every part of the request maps to a step." if not unparsed else \
            f"Unused words ({', '.join(unparsed)}) were confirmed as not changing the cohort."
        checks.append(Check("The whole request was turned into steps", "pass", detail))

    # 3. Frozen files
    used = {"sample_manifest.csv"}
    for s in steps:
        if s.get("op") == "join":
            used.add({"slides": "slide_manifest.csv", "clinical": "clinical.tsv", "survival": "survival.tsv",
                      "normals": "sample_manifest.csv"}[s["table"]])
        if s.get("op") == "mutation":
            used.add("mutation_gene_level.tsv.gz")
    bad = [f for f in used if store.file_status.get(f, {}).get("verified") is False]
    if bad:
        checks.append(Check("Files match the event's frozen checksums", "unknown",
                            f"{', '.join(bad)} changed since the frozen release.",
                            "Tell the organizers; don't use these results for judging."))
    else:
        checks.append(Check("Files match the event's frozen checksums", "pass",
                            "Every raw file used matches sources.csv (manifests are the committed versions).",
                            fields=sorted(used)))

    if errors:
        return _decide(checks, result, store)

    # 4. Join inflation
    inflated = [s for s in result.steps if s.inflated]
    if result.unresolved_inflation:
        worst = max(inflated, key=lambda s: s.rows_out - s.samples_out) if inflated else None
        where = f" It starts at step {worst.index + 1} ({worst.label})." if worst else ""
        checks.append(Check("No sample is counted twice", "fail",
                            f"The final table has {result.final_rows:,} rows for {result.final_samples:,} samples.{where}",
                            "Apply the suggested fix before exporting."))
    elif inflated:
        checks.append(Check("No sample is counted twice", "pass",
                            "A join inflated the rows, and a later step brought it back to one row per sample: "
                            + "; ".join(f"step {s.index + 1}" for s in inflated) + "."))
    elif spec.get("fix_log"):
        fixed = "; ".join(f"step {f['step']} had {f['rows']:,} rows for {f['samples']:,} samples and was fixed "
                          f"({f['fix'].rstrip('.')})" for f in spec["fix_log"])
        checks.append(Check("No sample is counted twice", "pass", f"Inflation found and fixed: {fixed}."))
    else:
        checks.append(Check("No sample is counted twice", "pass", "Every step kept one row per sample."))

    # 5. Patients counted twice through several samples
    pairs = any(s.get("op") == "join" and s.get("add_to_cohort") for s in steps)
    chooser = next((s for s in result.steps if s.op == "one_per_patient"), None)
    several_types = len((steps[0].get("values") if steps and steps[0].get("op") == "sample_type" else []) or []) > 1
    n_ids = len(result.cohort_ids)
    n_pat = store.manifest[store.manifest["sample_id"].isin(result.cohort_ids)]["patient_id"].nunique()
    if chooser is not None and n_ids == n_pat:
        how = describe_step(steps[chooser.index]).split(": ", 1)[-1]
        checks.append(Check("One sample per patient", "pass",
                            f"{n_ids:,} samples from {n_pat:,} patients. Step {chooser.index + 1} chose one sample for "
                            f"each of {len(chooser.selection):,} patients who had more than one, by this rule: {how}. "
                            "Every choice is listed in sample_selection.csv."))
    elif n_ids > n_pat and not (pairs or several_types):
        checks.append(Check("One sample per patient", "warn",
                            f"{n_ids:,} samples come from {n_pat:,} patients.",
                            "Decide whether each patient should count once, for example with a "
                            "'one sample per patient' step that records which sample it keeps."))
    elif n_ids > n_pat:
        checks.append(Check("One sample per patient", "pass",
                            f"{n_ids:,} samples from {n_pat:,} patients, as requested (more than one sample type)."))
    else:
        checks.append(Check("One sample per patient", "pass", f"{n_ids:,} samples from {n_pat:,} patients."))

    # 6. Size
    if n_pat < MIN_PATIENTS:
        checks.append(Check("Enough patients to work with", "fail",
                            f"Only {n_pat} patients remain (minimum {MIN_PATIENTS}).",
                            "Loosen a filter or choose a different question."))
    else:
        checks.append(Check("Enough patients to work with", "pass", f"{n_pat:,} patients (minimum {MIN_PATIENTS})."))

    # 7. Who did the steps remove? (composition shift)
    shifts, expected = [], []
    for s in result.steps:
        for var, info in s.shift.items():
            if info["tvd"] >= SHIFT_WARN and var != "Sample type":
                (expected if info.get("expected") else shifts).append((s, var, info))
    big_overall = {k: v for k, v in result.overall_shift.items() if v["tvd"] >= SHIFT_WARN and k != "Sample type"}
    overall = {k: v for k, v in big_overall.items() if not v.get("expected")}
    comp_cols = [c for c in store.composition_frame().columns if c not in ("patient_id",)]
    expected_note = ""
    if expected:
        expected_note = (" Expected, given what you filtered on: "
                         + "; ".join(f"step {s.index + 1} moved {var} by {info['tvd']:.2f} ({info['group']}: "
                                     f"{info['before_pct']}% → {info['after_pct']}%), because {info['expected']}"
                                     for s, var, info in expected[:3]) + ".")
    if shifts or overall:
        parts = [f"step {s.index + 1} moved {var} by {info['tvd']:.2f} (largest change: {info['group']}, "
                 f"{info['before_pct']}% → {info['after_pct']}%)" for s, var, info in shifts[:3]]
        parts += [f"overall, {var} moved by {info['tvd']:.2f} ({info['group']}: {info['before_pct']}% → {info['after_pct']}%)"
                  for var, info in list(overall.items())[:2]]
        checks.append(Check("The cohort still looks like the population it started from", "warn",
                            "; ".join(parts) + "." + expected_note,
                            "Check whether the shifted groups matter for your question, and report the shift.",
                            fields=comp_cols))
    elif expected:
        checks.append(Check("The cohort still looks like the population it started from", "pass",
                            "No unexpected shift." + expected_note, fields=comp_cols))
    else:
        checks.append(Check("The cohort still looks like the population it started from", "pass",
                            f"No step moved {', '.join(comp_cols)} by {SHIFT_WARN} or more.", fields=comp_cols))

    # 8. Filters on fields that are often missing
    gaps = [s for s in result.steps if s.op == "filter" and s.missing_fraction >= MISSING_WARN]
    if gaps:
        checks.append(Check("Filter fields are recorded for most samples", "warn",
                            "; ".join(f"step {s.index + 1}: {s.missing_fraction:.0%} of samples have no recorded value "
                                      f"for {s.column}, so they were dropped" for s in gaps) + ".",
                            "Missing values may not be random. Report how many were dropped and why."))
    else:
        checks.append(Check("Filter fields are recorded for most samples", "pass",
                            "Every filter field is recorded for at least 80% of the samples it was applied to."))

    # 9. Slide kinds and survival endpoints
    mixed = [s for s in steps if s.get("op") == "join" and s["table"] == "slides"
             and (not s.get("slide_kinds") or ("diagnostic (FFPE)" in s["slide_kinds"] and len(s["slide_kinds"]) > 1))]
    if mixed:
        checks.append(Check("Slides are of one kind", "warn",
                            "The slide join mixes diagnostic (FFPE) and frozen-section slides, which look different.",
                            "Choose diagnostic or frozen slides unless you need both."))
    os_used = [s for s in steps if s.get("op") == "filter" and s["table"] == "survival" and s["field"] == "os"]
    if os_used:
        checks.append(Check("Survival endpoint suits breast cancer", "warn",
                            "The TCGA Clinical Data Resource (Liu et al., Cell 2018) advises caution with overall "
                            "survival for BRCA because follow-up is short, and recommends the progression-free interval.",
                            "Consider PFI instead of OS."))
    return _decide(checks, result, store)


def _decide(checks, result, store):
    statuses = [c.status for c in checks]
    if "fail" in statuses:
        verdict = "Blocked"
    elif "unknown" in statuses:
        verdict = "Insufficient Evidence"
    elif "warn" in statuses:
        verdict = "Conditional"
    else:
        verdict = "Ready"
    deciding = next((c for c in checks if c.status == {"Blocked": "fail", "Insufficient Evidence": "unknown",
                                                         "Conditional": "warn"}.get(verdict)), None)
    reason = f"{deciding.name}: {deciding.detail}" if deciding else "Every check passed."
    review = ["Read each step and confirm it matches the cohort you meant."]
    review += [c.review for c in checks if c.status != "pass" and c.review]
    limitations = limitations_for(result, store)
    return Verdict(verdict, reason, checks, review, limitations=limitations)


def limitations_for(result, store):
    steps = result.spec.get("steps", [])
    out = ["Counts come from the event's frozen TCGA-BRCA release (UCSC Xena and NCI Imaging Data Commons). "
           "Other versions of TCGA can contain different samples.",
           "Composition shift thresholds (0.10) are conventions, not validated cut-offs. A shift shows who a step "
           "removed; it can't say why those samples lack data.",
           "This is a research tool. It makes no diagnosis, treatment recommendation or clinical claim."]
    fields = {s.get("field") for s in steps if s.get("op") == "filter"}
    if fields & {"er_status", "pr_status", "her2_status"}:
        out.append("ER, PR and HER2 status are clinical IHC results recorded by many hospitals with different "
                   "protocols. They disagree with RNA- or protein-based calls for some tumors.")
    if "pam50" in fields:
        out.append("PAM50 subtype is an RNA-based call. A different subtype call can give a different label for about "
                   "one tumor in ten (event kickoff figure).")
    layers = {s.get("layer") for s in steps if s.get("op") == "layer"}
    if layers & {"rna", "protein"}:
        out.append("Cohort Studio only chooses samples; it never changes values. The RNA file is already log2(RSEM + 1) "
                   "and the protein file uses replicate-based normalization (RBN), both as processed by UCSC Xena for "
                   "BRCA alone. If you re-normalize inside this cohort (for example z-scores), every value depends on "
                   "which samples are in it, so the same tumor can score differently in another cohort. Normalize the "
                   "same way for every cohort you compare, and report how.")
    if any(s.get("op") == "one_per_patient" for s in steps):
        out.append("Choosing one sample per patient follows the stated rule, not a judgment about which sample is best "
                   "for your question. sample_selection.csv lists every choice so a person can check it.")
    if any(s.get("op") == "mutation" for s in steps):
        out.append("Mutation status uses MC3 gene-level calls: samples without mutation data are removed, not "
                   "treated as wild-type.")
    if not store.available("clinical"):
        out.append("Clinical fields aren't loaded, so the shift check only covers submitting site and sample type.")
    return out
