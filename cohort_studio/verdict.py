"""Ready / Conditional / Blocked / Insufficient Evidence, with the checks behind it.

The thresholds are conventions chosen before looking at results (see VERDICT_RULES.md).
They are not validated cut-offs, and the app says so.
"""
from dataclasses import asdict, dataclass, field

from .spec import condition_sources, condition_tests, describe_step

MIN_PATIENTS = 20          # fewer patients than this: Blocked
SHIFT_WARN = 0.10          # total variation distance at or above this: Conditional
SHIFT_ALPHA = 0.05         # ...and random dropout of the same size moves it that far less often than this
MISSING_WARN = 0.20        # a filter field missing for at least this share of samples: Conditional
OUTCOME_COVERAGE = 0.80    # the stated outcome recorded for fewer cohort samples than this: Conditional
MIN_EVENTS = 10            # fewer events than this for a survival outcome: Conditional
SURVIVAL_EVENTS = ("os", "pfi", "dss", "dfi")
SOURCE_FILES = {"slides": "slide_manifest.csv", "clinical": "clinical.tsv", "survival": "survival.tsv",
                "normals": "sample_manifest.csv", "manifest": "sample_manifest.csv",
                "mutation": "mutation_gene_level.tsv.gz"}


def _condition_tests(steps):
    """Every test inside the 'where' steps (AND / OR / NOT conditions)."""
    return [t for s in steps if s.get("op") == "where" for t in condition_tests(s.get("condition") or {})]

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
    headline: str = ""          # the deciding problem in plain words, e.g. "The cohort's mix shifted"

    def to_dict(self):
        d = asdict(self)
        d["checks"] = [asdict(c) for c in self.checks]
        return d


def _is_unknown(message):
    m = message.lower()
    return "isn't available" in m or "checksum" in m or "fetch.py" in m or "aren't recorded" in m

def is_flagged_shift(info):
    """A shift counts against the cohort when it is at least SHIFT_WARN and random dropout of the same size would
    rarely cause it (permutation p < SHIFT_ALPHA). When the test can't run (for example a join added samples, or the
    characteristic has missing values) the TVD rule alone decides, as before."""
    if info["tvd"] < SHIFT_WARN:
        return False
    p = info.get("p_value")
    return p is None or p < SHIFT_ALPHA


def _within_chance(info):
    return info["tvd"] >= SHIFT_WARN and not is_flagged_shift(info)


def sign_off_text(spec):
    """'Confirmed by Name (role), 2026-11-16: note' for a named confirmation, or '' if nobody is named."""
    so = spec.get("sign_off") or {}
    if not so.get("name"):
        return ""
    who = so["name"] + (f" ({so['role']})" if so.get("role") else "")
    when = f", {so['at'][:10]}" if so.get("at") else ""
    note = f": {so['note']}" if so.get("note") else ""
    return f"Confirmed by {who}{when}{note}"


def _p_note(info):
    p = info.get("p_value")
    if p is None:
        return ""
    return "; p < 0.001 vs random dropout" if p <= 0.001 else f"; p = {p:.3f} vs random dropout"

# Words the parser knows, used to spot likely typos in unused words ("oder" → "older").
_KNOWN_WORDS = """older younger over under above below between aged age negative positive protein rppa rna expression
mutation mutated mutant wild-type stage triple basal luminal her2-enriched normal-like primary metastatic metastasis
tumour tumours tumor tumors normal adjacent matched survival overall progression-free deceased died dead alive
lobular ductal invasive infiltrating mucinous postmenopausal premenopausal perimenopausal menopause frozen diagnostic
slide slides copy number estrogen oestrogen progesterone receptor subtype radiation male female men women""".split()
_NUMBER_WORDS = set("""zero one two three four five six seven eight nine ten eleven twelve twenty thirty forty fifty sixty
seventy eighty ninety hundred""".split())


def explain_unused(words):
    """Say plainly what unused words might mean, so a too-broad cohort doesn't look finished."""
    import difflib
    lower = [w.lower() for w in words]
    notes = []
    real = [w for w in words if w.lower() in ("no", "not", "without", "except", "excluding", "never", "non",
                                              "neither", "nor")]
    neg = ([w for w in words if w.lower() == "but"][:1] + real) if real else []
    if neg:
        notes.append(f"'{' '.join(neg)}' wasn't applied, so the cohort may include what you asked to leave out")
    nums = [w for w in words if w[0] in "<>=0123456789"]
    if nums:
        notes.append(f"the number{'s' if len(nums) > 1 else ''} {', '.join(nums)} "
                     f"{'weren' if len(nums) > 1 else 'wasn'}'t used, so an age, stage or other limit may be missing")
    spelled = [w for w in words if any(p in _NUMBER_WORDS for p in w.lower().split("-"))]
    if spelled:
        notes.append(f"numbers written as words ({', '.join(spelled)}) aren't read: write them as digits")
    typos = []
    for w, lw in zip(words, lower):
        if lw in _KNOWN_WORDS or w in neg or w in nums or w in spelled:
            continue
        close = difflib.get_close_matches(lw, _KNOWN_WORDS, n=1, cutoff=0.75)
        if close:
            typos.append(f"'{w}' may mean '{close[0]}'")
    if typos:
        notes.append("; ".join(typos) + ", and that filter wasn't applied")
    head = f"These words were not used: {', '.join(words)}."
    if not notes:
        return head + " The filters they describe were NOT applied, so the cohort may be broader than you asked."
    return (head + " The cohort may be broader than you asked, because some filters were NOT applied: "
            + "; ".join(notes) + ".")


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
                            explain_unused(unparsed),
                            "Rewrite the request (for example 'stage 3' or 'older than 50') or add the missing step, "
                            "or confirm the unused words don't change the cohort."))
    else:
        signed = sign_off_text(spec)
        detail = "Every part of the request maps to a step." if not unparsed else \
            f"Unused words ({', '.join(unparsed)}) were confirmed as not changing the cohort." + \
            (f" {signed}." if signed else " Nobody was named as confirming this.")
        checks.append(Check("The whole request was turned into steps", "pass", detail))
    # 2b. Readings of the request that the draft settled without anyone choosing them (a default answer to a question,
    #     the only answer that runs, or a reading the comparison found open). Confirming the draft confirms them.
    unconfirmed = spec.get("unconfirmed") or []
    if unconfirmed and not spec.get("unparsed_confirmed"):
        checks.append(Check("Every open reading of the request was confirmed", "unknown",
                            "Nobody has confirmed how these parts of the request were read: " + "; ".join(unconfirmed)
                            + ". A different reading would select a different cohort.",
                            "Answer the question or edit the steps, or confirm these readings are what you meant."))
    elif unconfirmed:
        signed = sign_off_text(spec)
        checks.append(Check("Every open reading of the request was confirmed", "pass",
                            "A person confirmed these readings: " + "; ".join(unconfirmed) + "."
                            + (f" {signed}." if signed else " Nobody was named as confirming them.")))

    # 3. Frozen files
    used = {"sample_manifest.csv"}
    for s in steps:
        if s.get("op") == "join":
            used.add(SOURCE_FILES[s["table"]])
        if s.get("op") == "mutation":
            used.add(SOURCE_FILES["mutation"])
        if s.get("op") == "where":
            used |= {SOURCE_FILES[n] for n in condition_sources(s.get("condition") or {})}
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

    # 5b. Do the collection days make sense? (only when choosing the earliest or latest sample)
    if chooser is not None and steps[chooser.index].get("rule") in ("earliest", "latest"):
        types = store.manifest.set_index("sample_id")["sample_type"]
        odd = [x for x in chooser.selection
               if x["kept_type"] == "Metastatic" and any(types.get(d) == "Primary Tumor" for d in x["dropped"])
               and steps[chooser.index]["rule"] == "earliest"]
        no_day = [x for x in chooser.selection if "no collection day" in x["reason"]]
        if odd:
            checks.append(Check("Sample collection days make sense", "warn",
                                f"For {len(odd)} of the {len(chooser.selection)} patients it chose for, the collection days "
                                f"put a metastasis before the primary tumor (for example {odd[0]['patient_id']}), so "
                                "'earliest' kept the metastasis. In TCGA, days_to_collection may record when tissue was "
                                "procured for the project rather than when surgery happened.",
                                "Check the chosen samples in sample_selection.csv, or choose by sample type instead.",
                                fields=["days_to_collection"]))
        else:
            checks.append(Check("Sample collection days make sense", "pass",
                                f"No patient's metastasis is dated before their primary tumor"
                                + (f"; {len(no_day)} patients had no collection day and were chosen by sample type."
                                   if no_day else "."), fields=["days_to_collection"]))

    # 6. Size
    if n_pat < MIN_PATIENTS:
        checks.append(Check("Enough patients to work with", "fail",
                            f"Only {n_pat} patients remain (minimum {MIN_PATIENTS}).",
                            "Loosen a filter or choose a different question."))
    else:
        checks.append(Check("Enough patients to work with", "pass", f"{n_pat:,} patients (minimum {MIN_PATIENTS})."))

    # 7. Who did the steps remove? (composition shift)
    shifts, expected, chance = [], [], []
    for s in result.steps:
        for var, info in s.shift.items():
            if var == "Sample type" or info["tvd"] < SHIFT_WARN:
                continue
            if info.get("expected"):
                expected.append((s, var, info))
            elif is_flagged_shift(info):
                shifts.append((s, var, info))
            else:
                chance.append((s, var, info))
    overall = {k: v for k, v in result.overall_shift.items()
               if k != "Sample type" and not v.get("expected") and is_flagged_shift(v)}
    comp_cols = [c for c in store.composition_frame().columns if c not in ("patient_id",)]
    expected_note = ""
    if expected:
        expected_note = (" Expected, given what you filtered on: "
                         + "; ".join(f"step {s.index + 1} moved {var} by {info['tvd']:.2f} ({info['group']}: "
                                     f"{info['before_pct']}% → {info['after_pct']}%), because {info['expected']}"
                                     for s, var, info in expected[:3]) + ".")
    chance_note = ""
    if chance:
        chance_note = (" Within what random dropout of the same size produces, so not flagged: "
                       + "; ".join(f"step {s.index + 1} moved {var} by {info['tvd']:.2f} (p = {info['p_value']:.2f}; "
                                   f"random dropout gives up to {info.get('random_tvd_95', 0):.2f} in 95% of draws)"
                                   for s, var, info in chance[:3]) + ".")
    if shifts or overall:
        parts = [f"step {s.index + 1} moved {var} by {info['tvd']:.2f} (largest change: {info['group']}, "
                 f"{info['before_pct']}% → {info['after_pct']}%{_p_note(info)})" for s, var, info in shifts[:3]]
        parts += [f"overall, {var} moved by {info['tvd']:.2f} ({info['group']}: {info['before_pct']}% → {info['after_pct']}%{_p_note(info)})"
                  for var, info in list(overall.items())[:2]]
        checks.append(Check("The cohort still looks like the population it started from", "warn",
                            "; ".join(parts) + "." + expected_note + chance_note,
                            "Check whether the shifted groups matter for your question, and report the shift.",
                            fields=comp_cols))
    elif expected:
        checks.append(Check("The cohort still looks like the population it started from", "pass",
                            "No unexpected shift." + expected_note + chance_note, fields=comp_cols))
    elif chance:
        checks.append(Check("The cohort still looks like the population it started from", "pass",
                            "No shift beyond chance." + chance_note, fields=comp_cols))
    else:
        checks.append(Check("The cohort still looks like the population it started from", "pass",
                            f"No step moved {', '.join(comp_cols)} by {SHIFT_WARN} or more.", fields=comp_cols))

    # 8. Filters on fields that are often missing
    gaps = [s for s in result.steps if s.op in ("filter", "where") and s.missing_fraction >= MISSING_WARN]
    if gaps:
        checks.append(Check("Filter fields are recorded for most samples", "warn",
                            "; ".join(f"step {s.index + 1}: {s.missing_fraction:.0%} of samples " +
                                      ("couldn't be checked against the condition (a value it needs isn't recorded: "
                                       f"{s.column})" if s.op == "where" else f"have no recorded value for {s.column}")
                                      + ", so they were dropped" for s in gaps) + ".",
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
    os_used = [s for s in steps if s.get("op") == "filter" and s["table"] == "survival" and s["field"] == "os"] \
        + [t for t in _condition_tests(steps) if t.get("table") == "survival" and t.get("field") == "os"]
    use = spec.get("intended_use") or {}
    if os_used or use.get("outcome") == "os":
        checks.append(Check("Survival endpoint suits breast cancer", "warn",
                            "The TCGA Clinical Data Resource (Liu et al., Cell 2018) advises caution with overall "
                            "survival for BRCA because follow-up is short, and recommends the progression-free interval.",
                            "Consider PFI instead of OS."))
    # 10. The stated outcome: is it recorded, with enough events, for this cohort?
    if use.get("outcome"):
        checks.append(outcome_check(use, result, store, n_ids))
    return _decide(checks, result, store)


def outcome_check(use, result, store, n_ids):
    """Coverage (and, for a survival endpoint, the number of events) of the outcome the cohort is meant for."""
    from . import ops
    name = "The outcome you named is recorded for the cohort"
    field, table = use["outcome"], use.get("outcome_table") or (
        "survival" if use["outcome"] in ("os", "os_time", "pfi", "pfi_time", "dss", "dfi") else "clinical")
    if not store.available(table):
        return Check(name, "unknown", f"The {table} file isn't available, so {field} can't be checked.",
                     "Fetch the data with data/tcga-brca/fetch.py and run again.", fields=[field])
    col = store.resolve_field(table, field)
    t = store.table(table)
    if not col or col not in t.columns:
        return Check(name, "unknown", f"This release has no column for the outcome '{field}' in {table}.",
                     "Check the outcome's name (python -m cohort_studio --fields lists them).", fields=[field])
    t = t.drop_duplicates("sample_id").set_index("sample_id") if "sample_id" in t.columns else t
    values = t[col].reindex(list(result.cohort_ids))
    recorded = values[~values.map(ops.is_missing)]
    share = len(recorded) / n_ids if n_ids else 0
    label = store.field_label(table, field)
    detail = f"{label} ({col}) is recorded for {len(recorded):,} of {n_ids:,} samples ({share:.0%})"
    problems = []
    if share < OUTCOME_COVERAGE:
        problems.append(f"under {OUTCOME_COVERAGE:.0%}")
    if field in SURVIVAL_EVENTS:
        events = int((ops_numeric(recorded) == 1).sum())
        detail += f", with {events:,} events"
        if events < MIN_EVENTS:
            problems.append(f"fewer than {MIN_EVENTS} events")
    if problems:
        return Check(name, "warn", detail + " (" + " and ".join(problems) + ").",
                     "Samples without the outcome can't contribute to the analysis; report how many and why, or "
                     "choose a better-recorded outcome.", fields=[col])
    return Check(name, "pass", detail + ".", fields=[col])


def ops_numeric(series):
    import pandas as pd
    return pd.to_numeric(series, errors="coerce")


# What a failing check means, in plain words. The check names describe the passing state, so they read wrongly
# as the explanation of a problem ("The cohort still looks like the population..." when it doesn't).
PROBLEM_HEADLINES = {
    "Every step runs on this release": "A step can't run on this release",
    "The whole request was turned into steps": "Part of the request wasn't used",
    "Every open reading of the request was confirmed": "Part of the request could be read more than one way",
    "Files match the event's frozen checksums": "A data file doesn't match the frozen release",
    "No sample is counted twice": "Some samples are counted twice",
    "One sample per patient": "Some patients contribute more than one sample",
    "Sample collection days make sense": "The sample collection days look unreliable",
    "Enough patients to work with": "Too few patients",
    "The cohort still looks like the population it started from": "The cohort's mix shifted",
    "Filter fields are recorded for most samples": "A filter field is missing for many samples",
    "Slides are of one kind": "Diagnostic and frozen slides are mixed",
    "Survival endpoint suits breast cancer": "The survival endpoint needs care for breast cancer",
    "The outcome you named is recorded for the cohort": "The outcome is missing or rare in this cohort",
}


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
    headline = PROBLEM_HEADLINES.get(deciding.name, deciding.name) if deciding else "Every check passed"
    reason = f"{headline}. {deciding.detail}" if deciding else "Every check passed."
    review = ["Read each step and confirm it matches the cohort you meant."]
    review += [c.review for c in checks if c.status != "pass" and c.review]
    limitations = limitations_for(result, store)
    return Verdict(verdict, reason, checks, review, limitations=limitations, headline=headline)


def limitations_for(result, store):
    steps = result.spec.get("steps", [])
    out = ["Counts come from the event's frozen TCGA-BRCA release (UCSC Xena and NCI Imaging Data Commons). "
           "Other versions of TCGA can contain different samples.",
           "Composition shift thresholds (TVD 0.10, permutation p < 0.05) are conventions, not validated cut-offs. A "
           "shift shows who a step removed; it can't say why those samples lack data.",
           "This is a research tool. It makes no diagnosis, treatment recommendation or clinical claim."]
    if not (result.spec.get("intended_use") or {}).get("outcome"):
        out.append("No outcome was named, so the verdict doesn't check whether the outcome you will analyse is recorded "
                   "for this cohort. Name one (for example --outcome pfi) to add that check; coverage.csv lists every field.")
    fields = {s.get("field") for s in steps if s.get("op") == "filter"} \
        | {t.get("field") for t in _condition_tests(steps) if "table" in t}
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
    if any(s.get("op") == "one_per_patient" and s.get("rule") in ("earliest", "latest") for s in steps):
        out.append("'Earliest' and 'latest' use days_to_collection, which in TCGA may record when tissue was procured "
                   "for the project rather than when surgery happened, so it isn't a reliable surgical timeline.")
    if any(s.get("op") == "one_per_patient" for s in steps):
        out.append("Choosing one sample per patient follows the stated rule, not a judgment about which sample is best "
                   "for your question. sample_selection.csv lists every choice so a person can check it.")
    if any(s.get("op") == "mutation" for s in steps) or any("gene" in t for t in _condition_tests(steps)):
        out.append("Mutation status uses MC3 gene-level calls: samples without mutation data are removed, not "
                   "treated as wild-type.")
    if not store.available("clinical"):
        out.append("Clinical fields aren't loaded, so the shift check only covers submitting site and sample type.")
    # Organisers' rulings (Discord, 13 Nov): sample_manifest.csv is the official list of samples.
    try:
        ids = set(result.cohort_ids)
        if store.available("survival") and ids:
            col = store.resolve_field("survival", "redaction")
            surv = store.table("survival")
            if col and col in surv.columns:
                sid = surv["sample_id"] if "sample_id" in surv.columns else surv.index.to_series()
                red = sorted(set(sid[surv[col].astype(str).str.strip().str.lower() == "redacted"]) & ids)
                if red:
                    out.append(f"{len(red)} sample{'s' if len(red) > 1 else ''} in this cohort "
                               f"({', '.join(red[:3])}{', ...' if len(red) > 3 else ''}) "
                               f"{'have' if len(red) > 1 else 'has'} a survival record marked Redacted. "
                               "They stay in, because the sample manifest lists survival data for them, but a person "
                               "should check them before any survival analysis.")
        touches_normals_or_slides = any(
            (st.get("op") == "join" and st.get("table") in ("slides", "normals"))
            or (st.get("op") == "sample_type" and "Solid Tissue Normal" in st.get("values", []))
            for st in steps)
        if touches_normals_or_slides:
            slide_only = sorted(set(store.slides["sample_id"]) - set(store.manifest["sample_id"]))
            if slide_only:
                out.append(f"{len(slide_only)} samples appear in slide_manifest.csv but not in sample_manifest.csv "
                           f"(for example {slide_only[0]}). They are left out, because the sample manifest is the "
                           "official list of samples; the two files disagree about them.")
    except Exception:  # a limitation note must never stop the verdict
        pass
    return out
