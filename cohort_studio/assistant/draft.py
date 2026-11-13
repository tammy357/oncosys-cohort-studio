"""Draft a cohort with an AI model, grounded in the release, then turn the user's answers into a runnable spec.

1. The model sees the rules, the release's real fields/values/counts, and what this request needs (prompt.py).
2. Every step it writes is checked against the release; small fixes are made and recorded (verify.py).
3. Each question's options are run on the data, so the user sees how many samples each answer gives.
   A question whose answers give (nearly) the same cohort is resolved without asking.
4. A word that changes meaning (not, without, or, per...) that no step, question or note accounts for is
   flagged, so the verdict asks a person to confirm it.
"""
import copy
import json
import re

from .. import engine
from ..spec import condition_tests, describe_step, new_spec
from .catalog import Catalog
from .prompt import build_system, build_user
from .verify import Checker, _as_list, canonicalize, merge

MATERIAL_DIFFERENCE = 0.01      # answers whose cohorts differ by <= 1% aren't worth a question
MAX_QUESTIONS = 4
RISK_RE = re.compile(r"(?<![a-z])(not|no|without|except|excluding|exclude|non|never|neither|nor|or|only|per|each|"
                     r"either|aren't|isn't|didn't|don't|doesn't|wasn't|weren't|hasn't|haven't|other than)(?![a-z])")
NEGATIONS = {"not", "no", "without", "except", "excluding", "exclude", "non", "never", "neither", "aren't", "isn't",
             "didn't", "don't", "doesn't", "wasn't", "weren't", "hasn't", "haven't", "other than"}
GENE_WORDS = {"ERBB2": ("her2", "her-2", "erbb2"), "ESR1": ("esr1",)}
ENDPOINT_RE = re.compile(r"overall survival|(?<![a-z])os(?![a-z])|progress|(?<![a-z])pfi(?![a-z])|progression[- ]free|"
                         r"disease[- ]specific|(?<![a-z])dss(?![a-z])|disease[- ]free|(?<![a-z])dfi(?![a-z])")
EVENT_WORDS = [(r"progress", "pfi", "progression-free interval event"),
               (r"(?<![a-z])(died|death|deaths|dead|deceased|mortality)(?![a-z])", "os", "overall-survival event (death)"),
               (r"recur|relapse|came back", "dfi", "disease-free interval event (recurrence)")]
CLINICAL_OUTCOMES = {"vital_status", "person_neoplasm_cancer_status", "new_tumor_event_after_initial_treatment",
                     "new_neoplasm_event_type"}
CLINICAL_OUTCOME_NAMED = re.compile(r"vital status|tumou?r status|with tumou?r|tumou?r[- ]free|new tumou?r event|cancer status")


def _gene_named(low, gene):
    names = (gene.lower(),) + GENE_WORDS.get(gene, ())
    # 'tp-53', 'pik3-ca': a hyphen inside the symbol still names the gene (joined one way at a time, so a
    # describing word after it, as in 'tp-53-mutant', isn't glued on)
    letter_digit = re.sub(r"(?<=[a-z])[-_](?=\d)", "", low)
    digit_letter = re.sub(r"(?<=\d)[-_](?=[a-z])", "", low)
    return any(re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", text)
               for n in names for text in (low, letter_digit, digit_letter))


def _without_genes(node, genes):
    """The condition with the given gene tests removed (None if nothing is left)."""
    for key in ("all", "any"):
        if key in node:
            kids = [k for k in (_without_genes(x, genes) for x in node[key]) if k is not None]
            return None if not kids else kids[0] if len(kids) == 1 else {key: kids}
    if "not" in node:
        kid = _without_genes(node["not"], genes)
        return None if kid is None else {"not": kid}
    return None if node.get("gene") in genes else node


def _sanity_questions(request, base):
    """Catch three silent mistakes models make, and ask instead (every option is counted on the data later):
    a gene filter the request never named; a time limit on an unnamed survival clock; an outcome taken from the
    clinical record when the request means the curated survival event."""
    low = request.lower()
    keep, questions = [], []

    def ask(phrase, question, why, options):
        questions.append({"id": f"s{len(questions) + 1}", "phrase": phrase, "question": question, "why": why,
                          "source": "check", "options": options, "dropped_options": []})

    for s in base:
        invented = sorted({t["gene"] for t in condition_tests(s.get("condition") or {}) if "gene" in t
                           and not _gene_named(low, t["gene"])}) if s["op"] == "where" else []
        if invented:
            names = ", ".join(invented)
            reduced = _without_genes(s["condition"], set(invented))
            ask("", f"You didn't name {names}, but the draft's condition uses it. What did you mean?",
                "A gene in an AND or OR changes who is in the cohort.",
                [{"label": f"The condition without {names}" if reduced else f"Drop the condition (it only tests {names})",
                  "steps": [{**s, "condition": reduced}] if reduced else []},
                 {"label": f"Keep {names} in the condition", "steps": [s]}])
            continue
        if s["op"] == "mutation" and not _gene_named(low, s["gene"]):
            word = "mutated" if s.get("mutated", True) else "not mutated"
            ask("", f"You didn't name a gene, but the draft requires {s['gene']} {word}. What did you mean?",
                "Filtering on one gene keeps far fewer samples than requiring mutation data.",
                [{"label": "Any sample with mutation data", "steps": [{"op": "layer", "layer": "mutation"}]},
                 {"label": f"Only {s['gene']} {word}", "steps": [s]}])
            continue
        if (s["op"] == "filter" and s.get("table") == "survival" and s.get("field") in ("os_time", "pfi_time")
                and not ENDPOINT_RE.search(low)):
            ask("follow-up", "Which clock should the time limit use?",
                "Overall-survival time runs to death or last contact (the usual meaning of follow-up); "
                "progression-free time stops at progression.",
                [{"label": "Overall-survival time (to death or last contact)", "steps": [{**s, "field": "os_time"}]},
                 {"label": "Progression-free interval time", "steps": [{**s, "field": "pfi_time"}]}])
            continue
        if (s["op"] == "filter" and s.get("table") == "clinical" and s.get("field") in CLINICAL_OUTCOMES
                and not CLINICAL_OUTCOME_NAMED.search(low)):
            event = next(((f, words) for rx, f, words in EVENT_WORDS if re.search(rx, low)), None)
            if event:
                value = ", ".join(map(str, s.get("value") or []))
                ask("", "Which record should define the outcome?",
                    "The curated TCGA Clinical Data Resource and the original clinical record don't always agree.",
                    [{"label": f"Curated {event[1]} (TCGA-CDR, recommended)",
                      "steps": [{"op": "filter", "table": "survival", "field": event[0], "cmp": "is_event", "value": None}]},
                     {"label": f"Clinical record: {s['field']} is {value}", "steps": [s]}])
                continue
        keep.append(s)
    return keep, questions


def run_cohort(store, request, steps):
    """Run steps the way the app's one-click path does (run, fix inflation, run). Returns (sample ids or None, result)."""
    spec = new_spec(request, copy.deepcopy(steps))
    r = engine.run(spec, store)
    if any(s.inflated for s in r.steps):
        spec, _ = engine.apply_fixes(spec, r)
        r = engine.run(spec, store)
    return (set(r.cohort_ids) if r.ok else None), r


def _patients(store, ids):
    m = store.manifest
    return int(m.loc[m["sample_id"].isin(ids or []), "patient_id"].nunique())


def _choice(q, answers):
    i = (answers or {}).get(q["id"], q.get("recommended", 0))
    try:
        i = int(i)
    except (TypeError, ValueError):
        i = q.get("recommended", 0)
    return i if 0 <= i < len(q["options"]) else q.get("recommended", 0)


def materialize(draft, answers=None):
    steps = copy.deepcopy(draft["steps"])
    for q in draft.get("clarifications", []):
        steps = merge(steps, q["options"][_choice(q, answers)]["steps"])
    if draft.get("keep_order"):
        return steps
    return canonicalize(steps)[0]


def _str(x, default=""):
    return str(x).strip() if x not in (None, "") else default


def make_draft(request, store, client, current_spec=None, catalog=None, history=None, compare_with_rules=False,
               interactive=False):
    """Ask the model for a draft and check it. Raises AIError if the model can't be reached or replies badly.

    compare_with_rules: also draft the request with the built-in parser and keep the better draft or a mix
    (reconcile.py; at most one extra model call, only when the two select different samples). interactive: the
    user will answer the AI's questions, so a draft with open questions isn't compared."""
    catalog = catalog or Catalog.for_store(store)
    system, user = build_system(catalog), build_user(request, catalog, current_spec, history)
    data, usage, cached = client.complete_json(system, user)
    full = f"{current_spec['request']} | then: {request}" if current_spec and current_spec.get("request") else request
    draft = assemble(full, data, store, catalog, source=f"ai:{client.label}", typed=request)
    if compare_with_rules and not current_spec:
        from .reconcile import reconcile
        draft, extra, extra_cached = reconcile(draft, data, store, client, catalog, interactive=interactive)
        usage = {k: int((usage or {}).get(k, 0) or 0) + int((extra or {}).get(k, 0) or 0)
                 for k in set(usage or {}) | set(extra or {})}
        cached = cached and extra_cached
    draft.update(usage=usage, cached=cached, model=client.model, refines=bool(current_spec))
    return draft


def assemble(request, data, store, catalog=None, source="ai", typed=None):
    """Check a model reply (already parsed JSON) and build the draft the app shows."""
    catalog = catalog or Catalog.for_store(store)
    typed = typed or request
    chk = Checker(store, catalog)
    base = chk.check_steps(_as_list(data.get("steps")))
    corrections = [{"from": _str(c.get("from")), "to": _str(c.get("to")), "why": _str(c.get("why")), "source": "ai"}
                   for c in _as_list(data.get("corrections")) if isinstance(c, dict) and c.get("to")]
    unsupported = [{"phrase": _str(u.get("phrase"), "?"), "reason": _str(u.get("reason")),
                    "alternative": u.get("alternative") or None, "source": "ai"}
                   for u in _as_list(data.get("unsupported")) if isinstance(u, dict)]
    assumptions = [_str(a.get("text") if isinstance(a, dict) else a) for a in _as_list(data.get("assumptions")) if a]
    mappings = [{"phrase": _str(m.get("phrase")), "meaning": _str(m.get("meaning"))}
                for m in _as_list(data.get("mappings")) if isinstance(m, dict) and m.get("phrase")]
    questions = []
    for n, q in enumerate([q for q in _as_list(data.get("clarifications")) if isinstance(q, dict)][:3], 1):
        options, dropped = [], []
        for o in _as_list(q.get("options")):
            if not isinstance(o, dict):
                continue
            label = _str(o.get("label"), f"Option {len(options) + 1}")
            sub = Checker(store, catalog, allow_questions=False)
            o_steps = sub.check_steps(_as_list(o.get("steps")))
            if sub.unsupported or not o_steps:
                dropped.append({"label": label, "reason": "; ".join(u["reason"] for u in sub.unsupported)
                                or "no runnable steps"})
                continue
            corrections += [{**c, "why": f"{c['why']} (answer '{label}')"} for c in sub.corrections]
            options.append({"label": label, "steps": o_steps})
        q_out = {"id": f"q{n}", "phrase": _str(q.get("phrase")), "question": _str(q.get("question"), "Which do you mean?"),
                 "why": _str(q.get("why")), "source": "ai", "options": options, "dropped_options": dropped}
        if len(options) >= 2:
            questions.append(q_out)
        elif len(options) == 1:
            base = merge(base, options[0]["steps"])
            assumptions.append(f"{q_out['question']} Only one answer can run on this release, so it was used: "
                               f"{options[0]['label']}.")
        else:
            unsupported.append({"phrase": q_out["phrase"] or q_out["question"], "alternative": None, "source": "check",
                                "reason": "None of the suggested answers can run on this release: "
                                          + "; ".join(d["reason"] for d in dropped)})
    questions += chk.questions
    corrections += chk.corrections
    unsupported += chk.unsupported
    base, sanity = _sanity_questions(request, base)
    questions += sanity
    base, notes = canonicalize(base)
    if not any(s["op"] == "sample_type" for q in questions for o in q["options"] for s in o["steps"]):
        corrections += notes
    draft = {"request": request, "typed": typed, "source": source, "interpretation": _str(data.get("interpretation")),
             "steps": base, "mappings": mappings, "corrections": corrections, "clarifications": questions[:MAX_QUESTIONS],
             "auto_resolved": [], "unsupported": unsupported, "assumptions": assumptions, "warnings": [],
             "uncovered": []}
    _count_options(draft, store)
    _coverage(draft)
    _preview(draft, store)
    return draft


def draft_from_spec(spec, store, source):
    """Wrap a spec written without the model (built-in parser, advanced search) as a draft, steps kept as written."""
    draft = {"request": spec.get("request", ""), "typed": spec.get("request", ""), "source": source,
             "interpretation": "", "steps": copy.deepcopy(spec["steps"]), "keep_order": True,
             "mappings": [{"phrase": u["phrase"], "meaning": u["meaning"]} for u in spec.get("understood", [])],
             "corrections": [], "clarifications": [], "auto_resolved": [], "assumptions": [],
             "unsupported": [{"phrase": w, "reason": "not used by the built-in parser", "alternative": None, "source": source}
                             for w in spec.get("unparsed", [])],
             "warnings": [], "uncovered": [], "notes": list(spec.get("notes", [])), "prebuilt": copy.deepcopy(spec)}
    _preview(draft, store)
    return draft


def _count_options(draft, store):
    if not draft["clarifications"]:
        return
    sets = {}
    for q in draft["clarifications"]:
        q["recommended"] = 0
        sets[q["id"]] = []
        for i, o in enumerate(q["options"]):
            ids, _ = run_cohort(store, draft["request"], materialize(draft, {q["id"]: i}))
            o["n_samples"] = None if ids is None else len(ids)
            o["n_patients"] = None if ids is None else _patients(store, ids)
            o["labels"] = [describe_step(s) for s in o["steps"]]
            sets[q["id"]].append(ids)
    keep = []
    for q in draft["clarifications"]:
        s = sets[q["id"]]
        close = all(x is not None for x in s) and all(
            len(a ^ b) / max(1, len(a | b)) <= MATERIAL_DIFFERENCE for a in s for b in s)
        if close:
            draft["steps"] = merge(draft["steps"], q["options"][0]["steps"])
            counts = ", ".join(f"{o['label']}: {o['n_samples']}" for o in q["options"])
            draft["auto_resolved"].append({**q, "reason": f"Every answer gives (nearly) the same cohort ({counts}), "
                                                          f"so the first one was used without asking."})
        else:
            keep.append(q)
    draft["clarifications"] = keep
    if draft["auto_resolved"]:
        draft["steps"] = canonicalize(draft["steps"])[0]


def _coverage(draft):
    """Flag meaning-changing words that no mapping, question or unsupported note accounts for."""
    low = draft.get("typed", draft["request"]).lower()
    spans = []
    phrases = [m["phrase"] for m in draft["mappings"]] + [u["phrase"] for u in draft["unsupported"]]
    phrases += [q.get("phrase", "") for q in draft["clarifications"] + draft["auto_resolved"]]
    for p in phrases:
        p = (p or "").lower().strip()
        if len(p) >= 2:
            spans += [(m.start(), m.end()) for m in re.finditer(re.escape(p), low)]
    steps = materialize(draft)
    handled = set()
    if any(s["op"] == "any_of" or (isinstance(s.get("value"), list) and len(s["value"]) > 1) for s in steps) \
            or draft["clarifications"]:
        handled |= {"or", "either", "nor"}
    if any(s["op"] == "one_per_patient" for s in steps):
        handled |= {"per", "each"}
    if any(s["op"] == "exclude_layer" or s.get("cmp") == "not_in" or s.get("mutated") is False for s in steps):
        handled |= NEGATIONS
    trees = json.dumps([s.get("condition") for s in steps if s["op"] == "where"])
    if '"any"' in trees:
        handled |= {"or", "either", "nor"}
    if '"not"' in trees or '"mutated": false' in trees or '"not_in"' in trees:
        handled |= NEGATIONS
    uncovered = []
    for m in RISK_RE.finditer(low):
        word = m.group(1)
        if word in handled or any(a <= m.start() < b for a, b in spans):
            continue
        uncovered.append(word)
    draft["uncovered"] = list(dict.fromkeys(uncovered))
    if draft["uncovered"]:
        draft["warnings"].append("These words change meaning but no step accounts for them: "
                                 + ", ".join(f"'{w}'" for w in draft["uncovered"]) + ". Check the steps.")


def _preview(draft, store):
    steps = materialize(draft)
    ids, r = run_cohort(store, draft["request"], steps)
    draft["step_labels"] = [describe_step(s) for s in draft["steps"]]
    draft["preview"] = {"n_samples": None if ids is None else len(ids),
                        "n_patients": None if ids is None else _patients(store, ids),
                        "errors": [f"Step {s.index + 1}: {s.error}" for s in r.steps
                                   if s.error and s.error != "Not run because an earlier step failed."]}
    for s in r.steps:
        if not s.skipped and s.samples_in and s.samples_out == 0:
            draft["warnings"].append(f"Step {s.index + 1} ({s.label}) leaves no samples. Check its values.")
    substantive = [s for s in steps if s["op"] != "sample_type" and not (s["op"] == "join" and s.get("table") in ("clinical", "survival"))]
    if draft["clarifications"]:
        draft["status"] = "needs_answers"
    elif not substantive and draft["unsupported"]:
        draft["status"] = "cannot_answer"
    else:
        draft["status"] = "ready"


def to_spec(draft, answers=None):
    """The runnable spec for a draft and the user's answers (unanswered questions use the recommended answer)."""
    if draft.get("prebuilt"):
        return copy.deepcopy(draft["prebuilt"])
    answers = answers or {}
    steps = materialize(draft, answers)
    spec = new_spec(draft["request"], steps, parser=draft["source"])
    chosen = []
    for q in draft["clarifications"]:
        o = q["options"][_choice(q, answers)]
        chosen.append({"question": q["question"], "answer": o["label"], "n_samples": o.get("n_samples"),
                       "answered_by": "you" if q["id"] in answers else "default"})
    notes = [f"Steps drafted by {draft.get('model') or draft['source']} and checked against this release. "
             "Check each one before running."]
    notes += [f"Corrected '{c['from']}' to '{c['to']}' ({c['why']})." for c in draft["corrections"] if c.get("from")]
    notes += [f"{c['question']} Answer: {c['answer']}"
              + (" (the recommended answer; nobody confirmed it)" if c["answered_by"] == "default" else "")
              for c in chosen]
    notes += [a["reason"] for a in draft["auto_resolved"]]
    notes += [f"Assumed: {a}" for a in draft["assumptions"]]
    notes += [f"Not in this release: {u['phrase']}. {u['reason']}" for u in draft["unsupported"]]
    notes += draft.get("review_notes", [])
    spec["notes"] = notes
    spec["unparsed"] = ([u["phrase"] for u in draft["unsupported"]] + draft["uncovered"]
                        + [c["question"] for c in chosen if c["answered_by"] == "default"])
    spec["understood"] = draft["mappings"]
    spec["assistant"] = {"source": draft["source"], "model": draft.get("model"), "interpretation": draft["interpretation"],
                         "answers": chosen, "corrections": draft["corrections"], "assumptions": draft["assumptions"],
                         "unsupported": draft["unsupported"], "uncovered": draft["uncovered"],
                         "reconcile": draft.get("reconcile")}
    return spec


def review_items(spec):
    """Extra 'a person must check' items for a spec the assistant drafted."""
    a = spec.get("assistant") or {}
    items = [f"Confirm the assumption: {x}" for x in a.get("assumptions", [])]
    items += [f"Confirm the default answer to: {c['question']} ({c['answer']})"
              for c in a.get("answers", []) if c.get("answered_by") == "default"]
    if a.get("corrections"):
        items.append("Check the corrections the assistant made to your wording (listed with the steps).")
    return items
