"""Check the AI draft against the built-in parser's draft for the same request, and keep the better one or a mix.

The built-in parser (cohort_studio/parse.py) is exact on the phrases it knows and skips words it doesn't; the AI
reads any wording but can add, drop or misread a constraint. They are compared by the samples they select:
* the same samples: nothing more to do (no extra model call);
* one of them can't run: the other is used (no extra model call);
* different samples: one short model call sees both drafts, their counts and the words the parser didn't use,
  and picks the AI draft, the parser's draft, or a mix. A mix may only reuse steps that appear in one of the two
  drafts, and whatever is picked is checked and run like any other draft before it's used.
"""
import copy
import json

from ..parse import parse_request
from ..spec import describe_step
from .client import AIError
from .draft import _patients, assemble, draft_from_spec, materialize, run_cohort

REVIEW_SYSTEM = """You check two drafts of the same TCGA-BRCA cohort request and keep the one that does exactly what \
the request asks.
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
If both are equally faithful, answer "ai". Sample counts show how much a difference matters; they are not a reason \
on their own.
OUTPUT: only this JSON object: {"choice":"ai"|"rules"|"merge","steps":[...] (only for merge),"reason":"one sentence \
naming the difference that decided it"}"""


def _key(step):
    return json.dumps(step, sort_keys=True)


def _n(ids):
    return None if ids is None else len(ids)


def _lines(steps):
    return [f"  {i}. {describe_step(s)}   {_key(s)}" for i, s in enumerate(steps, 1)] or ["  (no steps)"]


def review_message(request, ai_draft, ai_steps, ai_ids, rules, rules_ids, store):
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
    return "\n".join(lines)


def reconcile(draft, data, store, client, catalog, interactive=False):
    """Return (draft to use, usage of the review call or None, cached). See the module docstring."""
    if interactive and draft.get("clarifications"):
        return draft, None, True             # the user answers the AI's questions; their answer decides
    rules = parse_request(draft.get("typed") or draft["request"], store)
    ai_steps = materialize(draft)
    ai_ids, _ = run_cohort(store, draft["request"], ai_steps)
    rules_ids, _ = run_cohort(store, draft["request"], rules["steps"]) if rules["steps"] else (None, None)
    info = {"ai_samples": _n(ai_ids), "rules_samples": _n(rules_ids), "rules_steps": rules["steps"],
            "rules_unparsed": rules.get("unparsed") or []}

    def keep(decision, note, usage=None, cached=True):
        draft["reconcile"] = {**info, "decision": decision, "note": note}
        draft.setdefault("review_notes", []).append(note)
        return draft, usage, cached

    if rules_ids is None:
        return keep("ai", "The built-in parser couldn't draft runnable steps for this request, so the AI draft was used.")
    if ai_ids is None:
        return _use_rules(draft, rules, store, info, "The AI draft can't run on this release, so the built-in "
                                                     "parser's steps were used."), None, True
    if ai_ids == rules_ids:
        return keep("same", f"The built-in parser drafted the same cohort ({len(ai_ids)} samples).")

    try:
        reply, usage, cached = client.complete_json(
            REVIEW_SYSTEM, review_message(draft.get("typed") or draft["request"], draft, ai_steps, ai_ids, rules,
                                          rules_ids, store))
    except AIError as exc:
        return keep("ai", f"The built-in parser drafted a different cohort ({len(rules_ids)} samples vs "
                          f"{len(ai_ids)}); the comparison couldn't run ({exc}), so the AI draft was kept.")
    choice = str(reply.get("choice", "")).strip().lower()
    reason = str(reply.get("reason") or "").strip()[:300]
    head = f"Compared with the built-in parser ({len(rules_ids)} samples) and the AI draft ({len(ai_ids)} samples): "
    if choice == "rules":
        return _use_rules(draft, rules, store, info, head + f"kept the parser's steps. {reason}"), usage, cached
    if choice == "merge":
        merged = _merge(draft, data, reply.get("steps"), ai_steps, rules["steps"], store, catalog)
        if merged is not None:
            merged["reconcile"] = {**info, "decision": "merge", "note": head + f"combined their steps. {reason}",
                                   "final_samples": merged["preview"]["n_samples"]}
            merged["review_notes"] = [merged["reconcile"]["note"]]
            return merged, usage, cached
        return keep("ai", head + "a mix was suggested but it reused steps from neither draft or couldn't run, so the "
                                 "AI draft was kept.", usage, cached)
    return keep("ai", head + (f"kept the AI draft. {reason}" if choice == "ai"
                              else "the comparison gave no clear answer, so the AI draft was kept."), usage, cached)


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


def _merge(draft, data, steps, ai_steps, rules_steps, store, catalog):
    allowed = {_key(s) for s in ai_steps} | {_key(s) for s in rules_steps}
    if not isinstance(steps, list) or not steps or any(not isinstance(s, dict) or _key(s) not in allowed for s in steps):
        return None
    out = assemble(draft["request"], {"interpretation": data.get("interpretation"), "steps": steps,
                                      "mappings": data.get("mappings"), "unsupported": data.get("unsupported"),
                                      "assumptions": data.get("assumptions")},
                   store, catalog, source=draft["source"], typed=draft.get("typed"))
    if out["preview"]["n_samples"] is None or any(u["source"] == "check" for u in out["unsupported"]):
        return None
    return out
