"""Check the AI draft against the built-in parser's draft for the same request, and keep the better one or a mix.

The built-in parser (cohort_studio/parse.py) is exact on the phrases it knows and skips words it doesn't; the AI
reads any wording but can add, drop or misread a constraint. They are compared by the samples they select:
* the same samples, and every word of the request used by one of them: nothing more to do (no extra model call);
* one of them can't run: the other is used (no extra model call);
* the AI draft selects no samples and the parser's selects some: the parser's is used (no extra model call). An
  empty AI cohort usually means a step that can't hold (e.g. a 'not one of' list naming every recorded value), and
  the parser's unused words stay flagged for a person to confirm. Not when the parser skipped a word the AI draft
  carries out: then the empty cohort may be the right answer, and the comparison below decides;
* otherwise one short model call sees both drafts, their counts and the words neither used. It picks the AI draft,
  the parser's draft, or a mix (only when they select different samples), and audits the kept draft: parts of the
  request no step carries out become unused words, and readings it settled without asking become unconfirmed, so
  the verdict can't say Ready until a person checks them. The audit is a second opinion, so it only counts where the
  drafts back it up (findings()). A mix may only reuse steps that appear in one of the two drafts; a draft or mix
  that selects no samples never replaces one that selects some; the parser's draft never replaces the AI's when it
  skips a word the AI draft carries out; whatever is kept is checked and run like any other draft before it's used.
A number the parser couldn't place that the kept draft turned into a limit is always an unconfirmed reading
(numbers_read_as_limits()): the request doesn't say what the number measures.
"""
import copy
import json
import re

from ..parse import parse_request
from ..spec import condition_tests, describe_step
from .client import AIError
from .draft import _patients, assemble, draft_from_spec, materialize, run_cohort

MAX_FINDINGS = 4
NUMBER = re.compile(r"^[<>=]*(\d+(?:\.\d+)?)\+?%?$")

REVIEW_SYSTEM = """You check two drafts of the same TCGA-BRCA cohort request: keep the one that does exactly what \
the request asks, then say what the kept draft still doesn't do.
A was written by an AI model: it reads any wording, but it sometimes adds a constraint nobody asked for, drops one, \
or picks the wrong field or value.
B was written by a rule-based parser: exact on the phrases it knows, but it silently skips words it doesn't know \
(they are listed), and it combines every step with AND, so it can't express OR across different fields, tables, \
layers or genes, or NOT of a group of conditions.
Decide from the request's literal meaning:
- "ai": A does what the request asks and B misses or misreads part of it.
- "rules": B does what the request asks and A adds, drops or changes something.
- "merge": each draft gets a different part right. Give the complete merged step list, copying every step exactly \
from A or B. Never write a step that is in neither draft.
If both are equally faithful, answer "ai". A draft that selects no samples can't be kept when the other selects some. \
Sample counts show how much a difference matters; they are not a reason on their own. A stage-group filter \
("cmp":"stage_in", shown as "is stage II") includes every sub-stage of the group (IIA, IIB, ...); a list of exact \
stage values includes only the values listed.
Then read the request part by part against the draft you kept. The steps build one set of samples: they can't make \
two groups to compare, split, balance, sample or match.
- "not_done": parts of the request that no step of the kept draft carries out in any form and that A doesn't \
already list as not in this release. A part that some step carries out, even in a form you would write differently, \
is done. Copy the exact words from the request.
- "unsettled": parts with more than one reasonable reading in this release that the kept draft settled without \
asking (for example a number with no unit or field, or a term with several accepted definitions). Copy the exact \
words and give the reading the draft used.
Leave both lists empty when the kept draft does everything the request asks.
OUTPUT: only this JSON object: {"choice":"ai"|"rules"|"merge","steps":[...] (only for merge),"reason":"one sentence \
naming the difference that decided it","not_done":["exact words"],"unsettled":[{"phrase":"exact words",\
"read_as":"the reading used"}]}"""


def _key(step):
    return json.dumps(step, sort_keys=True)


def _n(ids):
    return None if ids is None else len(ids)


def _lines(steps):
    return [f"  {i}. {describe_step(s)}   {_key(s)}" for i, s in enumerate(steps, 1)] or ["  (no steps)"]


def _norm(text):
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def _claimed_spans(draft, low, carried_out_only=False):
    """Where in the request the AI draft says it did something: its mappings and questions, and (unless only what it
    carries out is wanted) what it lists as not in this release."""
    phrases = [m.get("phrase") for m in draft.get("mappings", [])]
    phrases += [] if carried_out_only else [u.get("phrase") for u in draft.get("unsupported", [])]
    phrases += [q.get("phrase") for q in draft.get("clarifications", []) + draft.get("auto_resolved", [])]
    phrases += [u.get("phrase") for u in draft.get("unconfirmed", [])]
    spans = []
    for p in phrases:
        p = _norm(p)
        if len(p) >= 2:
            spans += [(m.start(), m.end()) for m in re.finditer(re.escape(p), low)]
    return spans


def _skipped(rules, request, spans, inside):
    low = _norm(request)
    out = []
    for w in rules.get("unparsed") or []:
        hits = [m.start() for m in re.finditer(rf"(?<![a-z0-9]){re.escape(w.lower())}(?![a-z0-9])", low)]
        if hits and any(a <= h < b for h in hits for a, b in spans) == inside:
            out.append(w)
    return list(dict.fromkeys(out))


def unaccounted_words(draft, rules, request):
    """Words the parser couldn't use that the AI draft doesn't account for either: nobody carried them out."""
    return _skipped(rules, request, _claimed_spans(draft, _norm(request)), inside=False)


def skipped_but_carried_out(draft, rules, request):
    """Words the parser skipped that the AI draft carries out: choosing the parser's steps would drop them."""
    return _skipped(rules, request, _claimed_spans(draft, _norm(request), carried_out_only=True), inside=True)


def _limits(step):
    """The numbers a step uses as limits (gte / lte / between), wherever the step keeps its tests."""
    op = step.get("op")
    tests = ([step] if op == "filter" else list(step.get("conditions") or []) if op == "any_of"
             else condition_tests(step.get("condition") or {}) if op == "where" else [])
    out = []
    for t in tests:
        if t.get("cmp") in ("gte", "lte", "between"):
            v = t.get("value")
            out += [x for x in (v if isinstance(v, list) else [v]) if isinstance(x, (int, float))]
    return out


def numbers_read_as_limits(rules, steps):
    """Numbers the parser couldn't place that the draft turned into a limit. The parser reads the stated forms ('over
    50', 'aged 50+', '5 years of follow-up'); when it can't place a number, the request doesn't say plainly what the
    number measures, so the draft's reading of it is a guess a person must confirm."""
    out = []
    for w in rules.get("unparsed") or []:
        m = NUMBER.match(w)
        if not m:
            continue
        n = float(m.group(1))
        step = next((s for s in steps for v in _limits(s)
                     if abs(v - n) <= 1 or abs(v - round(n * 365.25)) <= 1), None)   # 'under 50' -> 49; years -> days
        if step is not None:
            out.append({"phrase": w, "read_as": describe_step(step), "source": "check"})
    return out


def review_message(request, ai_draft, ai_steps, ai_ids, rules, rules_ids, store, unaccounted=()):
    a_keys, b_keys = {_key(s) for s in ai_steps}, {_key(s) for s in rules["steps"]}
    lines = [f"REVIEW: {request}", "",
             f"A (AI draft): {_n(ai_ids)} samples, {_patients(store, ai_ids)} patients", *_lines(ai_steps)]
    if ai_draft.get("interpretation"):
        lines.append(f"  A read the request as: {ai_draft['interpretation']}")
    for q in ai_draft.get("clarifications", []):
        lines.append(f"  A asked: {q['question']} (its first answer is used: {q['options'][0]['label']})")
    for u in ai_draft.get("unsupported", []):
        lines.append(f"  A says not in this release: {u['phrase']} ({u['reason']})")
    if ai_draft.get("uncovered"):
        lines.append(f"  words no step of A accounts for: {', '.join(ai_draft['uncovered'])}")
    lines += ["", f"B (built-in parser): {_n(rules_ids)} samples, {_patients(store, rules_ids)} patients",
              *_lines(rules["steps"])]
    lines.append(f"  words the parser didn't use: {', '.join(rules.get('unparsed') or []) or 'none'}")
    lines += ["", "Steps only in A: " + ("; ".join(describe_step(s) for s in ai_steps if _key(s) not in b_keys) or "none"),
              "Steps only in B: " + ("; ".join(describe_step(s) for s in rules["steps"] if _key(s) not in a_keys) or "none")]
    if unaccounted:
        lines.append("Words of the request that neither draft uses: " + ", ".join(unaccounted))
    if ai_ids == rules_ids:
        lines.append("Both drafts select the same samples: keep A and only audit it.")
    return "\n".join(lines)


def _has_word(phrase, words):
    return any(re.search(rf"(?<![a-z0-9]){re.escape(w.lower())}(?![a-z0-9])", phrase) for w in words)


def findings(reply, request, draft, unaccounted, parser_unused):
    """The review's audit, kept only where the drafts themselves back it up. The review is a second reading, and it
    can be wrong, so each finding must quote the request and rest on what the drafts did:
    * a part 'not done' must contain a word neither draft used (the parser skipped it and the AI draft doesn't
      account for it), so a part the AI draft maps to a step is never reported as missing;
    * an 'unsettled' reading must contain a word the parser couldn't read literally (what it reads has one reading)."""
    low = _norm(request)
    known = [_norm(p) for p in [u.get("phrase") for u in draft.get("unsupported", [])]
             + [q.get("phrase") for q in draft.get("clarifications", [])] if _norm(p)]

    def quoted(p, words):
        p = _norm(p).strip(" .,;:'\"")
        ok = len(p) >= 2 and p in low and _has_word(p, words) and not any(p in k or k in p for k in known)
        return p if ok else None

    not_done, unsettled = [], []
    for item in reply.get("not_done") or []:
        p = quoted(item.get("phrase") if isinstance(item, dict) else item, unaccounted)
        if p and p not in not_done:
            not_done.append(p)
    for item in reply.get("unsettled") or []:
        p = quoted(item.get("phrase") if isinstance(item, dict) else item, parser_unused)
        read_as = str(item.get("read_as") or "").strip()[:200] if isinstance(item, dict) else ""
        if p and p not in not_done and p not in [u["phrase"] for u in unsettled]:
            unsettled.append({"phrase": p, "read_as": read_as or "the draft's steps", "source": "review"})
    return not_done[:MAX_FINDINGS], unsettled[:MAX_FINDINGS]


def _apply_findings(out, rules, not_done=(), unsettled=()):
    """Put what is still open where the verdict reads it: parts no step carries out become unused words; readings
    nobody chose (the review's, and numbers the parser couldn't place that the draft made into limits) become
    unconfirmed."""
    spec = out.get("prebuilt")
    if spec is None:                         # an AI draft or a mix: check how it used the numbers too
        have = [u["phrase"] for u in list(unsettled) + out.get("unconfirmed", [])]
        unsettled = list(unsettled) + [u for u in numbers_read_as_limits(rules, materialize(out))
                                       if u["phrase"] not in have]
    if not not_done and not unsettled:
        return out
    out.setdefault("unsupported", []).extend({"phrase": p, "reason": "no step carries it out (found when the draft "
                                              "was compared with the built-in parser)", "alternative": None,
                                              "source": "review"} for p in not_done)
    out.setdefault("unconfirmed", []).extend(unsettled)
    if spec is not None:                     # the parser's steps: its spec is what runs
        spec["unparsed"] = list(dict.fromkeys(list(spec.get("unparsed") or []) + list(not_done)))
        spec["unconfirmed"] = list(spec.get("unconfirmed") or []) + [f"{u['phrase']} (read as: {u['read_as']})"
                                                                      for u in unsettled]
    return out


def reconcile(draft, data, store, client, catalog, interactive=False):
    """Return (draft to use, usage of the review call or None, cached). See the module docstring."""
    if interactive and draft.get("clarifications"):
        return draft, None, True             # the user answers the AI's questions; their answer decides
    request = draft.get("typed") or draft["request"]
    rules = parse_request(request, store)
    ai_steps = materialize(draft)
    ai_ids, _ = run_cohort(store, draft["request"], ai_steps)
    rules_ids, _ = run_cohort(store, draft["request"], rules["steps"]) if rules["steps"] else (None, None)
    info = {"ai_samples": _n(ai_ids), "rules_samples": _n(rules_ids), "rules_steps": rules["steps"],
            "rules_unparsed": rules.get("unparsed") or []}
    usage, cached, not_done, unsettled = None, True, [], []

    def done(out):
        return _apply_findings(out, rules, not_done, unsettled), usage, cached

    def keep(decision, note):
        draft["reconcile"] = {**info, "decision": decision, "note": note}
        draft.setdefault("review_notes", []).append(note)
        return done(draft)

    if rules_ids is None:
        return keep("ai", "The built-in parser couldn't draft runnable steps for this request, so the AI draft was used.")
    if ai_ids is None:
        return done(_use_rules(draft, rules, store, info, "The AI draft can't run on this release, so the built-in "
                                                          "parser's steps were used."))
    if not ai_ids and rules_ids and not skipped_but_carried_out(draft, rules, request):
        return done(_use_rules(draft, rules, store, info, f"The AI draft selects no samples and the built-in parser's "
                                                          f"selects {len(rules_ids)}, so the parser's steps were used."))
    unaccounted = unaccounted_words(draft, rules, request)
    same = ai_ids == rules_ids
    same_note = f"The built-in parser drafted the same cohort ({len(ai_ids)} samples)."
    if same and not unaccounted:
        return keep("same", same_note)

    try:
        reply, usage, cached = client.complete_json(
            REVIEW_SYSTEM, review_message(request, draft, ai_steps, ai_ids, rules, rules_ids, store, unaccounted))
    except AIError as exc:
        return keep("same", same_note) if same else keep(
            "ai", f"The built-in parser drafted a different cohort ({len(rules_ids)} samples vs {len(ai_ids)}); the "
                  f"comparison couldn't run ({exc}), so the AI draft was kept.")
    reply = reply if isinstance(reply, dict) else {}
    not_done, unsettled = findings(reply, request, draft, unaccounted, rules.get("unparsed") or [])
    if same:
        return keep("same", same_note + (" Checked the words neither draft used." if not_done or unsettled else ""))
    choice = str(reply.get("choice", "")).strip().lower()
    reason = str(reply.get("reason") or "").strip()[:300]
    head = f"Compared with the built-in parser ({len(rules_ids)} samples) and the AI draft ({len(ai_ids)} samples): "
    if choice == "rules" and not rules_ids and ai_ids:
        return keep("ai", head + "the parser's steps were preferred, but they select no samples (their conditions "
                                 "can't all hold at once), so the AI draft was kept.")
    dropped = skipped_but_carried_out(draft, rules, request) if choice == "rules" else []
    if dropped:
        return keep("ai", head + "the parser's steps were preferred, but they leave out "
                    + ", ".join(f"'{w}'" for w in dropped) + ", which the AI draft carries out, so the AI draft was kept.")
    if choice == "rules":
        return done(_use_rules(draft, rules, store, info, head + f"kept the parser's steps. {reason}"))
    if choice == "merge":
        merged = _merge(draft, data, reply.get("steps"), ai_steps, rules["steps"], store, catalog, bool(ai_ids))
        if merged is not None:
            merged["reconcile"] = {**info, "decision": "merge", "note": head + f"combined their steps. {reason}",
                                   "final_samples": merged["preview"]["n_samples"]}
            merged["review_notes"] = [merged["reconcile"]["note"]]
            return done(merged)
        return keep("ai", head + "a mix was suggested but it reused steps from neither draft, couldn't run or selected "
                                 "no samples, so the AI draft was kept.")
    return keep("ai", head + (f"kept the AI draft. {reason}" if choice == "ai"
                              else "the comparison gave no clear answer, so the AI draft was kept."))


def _use_rules(draft, rules, store, info, note):
    """The parser's steps exactly as it wrote them (its unused words stay flagged for a person to confirm)."""
    spec = copy.deepcopy(rules)
    spec["notes"] = list(spec.get("notes", [])) + [note]
    spec["parser"] = f"{draft['source']} -> built-in parser"
    out = draft_from_spec(spec, store, source=spec["parser"])
    out.update(interpretation=draft.get("interpretation", ""), typed=draft.get("typed"),
               reconcile={**info, "decision": "rules", "note": note}, review_notes=[note])
    spec["assistant"] = {"source": draft["source"], "reconcile": out["reconcile"]}
    out["prebuilt"] = spec
    return out


def _merge(draft, data, steps, ai_steps, rules_steps, store, catalog, ai_has_samples=False):
    allowed = {_key(s) for s in ai_steps} | {_key(s) for s in rules_steps}
    if not isinstance(steps, list) or not steps or any(not isinstance(s, dict) or _key(s) not in allowed for s in steps):
        return None
    out = assemble(draft["request"], {"interpretation": data.get("interpretation"), "steps": steps,
                                      "mappings": data.get("mappings"), "unsupported": data.get("unsupported"),
                                      "assumptions": data.get("assumptions")},
                   store, catalog, source=draft["source"], typed=draft.get("typed"))
    n = out["preview"]["n_samples"]
    if n is None or (n == 0 and ai_has_samples) or any(u["source"] == "check" for u in out["unsupported"]):
        return None
    return out
