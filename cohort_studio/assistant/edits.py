"""Editing a built cohort: what changed between two sets of steps, and the joins its filters need.

A step's identity is what it is about (the field a filter tests, the layer it requires, the gene, the table it
joins), so "ER status is Positive" -> "ER status is Negative" reads as one change rather than a removal plus an
addition. Joins to the clinical and survival tables are bookkeeping the engine needs; they are added
automatically and left out of change lists.
"""
import json

from ..spec import describe_step

_AUTO_JOINS = ("clinical", "survival")


def _clean(step):
    return {k: v for k, v in step.items() if not str(k).startswith("_")}


def _key(step):
    return json.dumps(_clean(step), sort_keys=True)


def signature(step):
    op = step.get("op")
    if op == "filter":
        return ("filter", step.get("table"), step.get("field"))
    if op in ("layer", "exclude_layer"):
        return ("layer", step.get("layer"))
    if op == "mutation":
        return ("mutation", str(step.get("gene", "")).upper())
    if op == "join":
        return ("join", step.get("table"))
    if op == "any_of":
        return ("any_of", step.get("table"), tuple(sorted(str(c.get("field")) for c in step.get("conditions", []))))
    return (op,)


def _bookkeeping(step):
    return step.get("op") == "join" and step.get("table") in _AUTO_JOINS and step.get("key", "sample_id") == "sample_id"


def diff_steps(old, new):
    """[{kind: added|removed|changed, before, after}] in reading order; bookkeeping joins are ignored."""
    old = [_clean(s) for s in old or [] if not _bookkeeping(s)]
    new = [_clean(s) for s in new or [] if not _bookkeeping(s)]
    by_sig = {}
    for i, s in enumerate(old):
        by_sig.setdefault(signature(s), []).append(i)
    used, changes = set(), []
    for s in new:
        cands = [i for i in by_sig.get(signature(s), []) if i not in used]
        same = next((i for i in cands if _key(old[i]) == _key(s)), None)
        match = same if same is not None else (cands[0] if cands else None)
        if match is None:
            changes.append({"kind": "added", "before": None, "after": describe_step(s)})
            continue
        used.add(match)
        if _key(old[match]) != _key(s):
            changes.append({"kind": "changed", "before": describe_step(old[match]), "after": describe_step(s)})
    for i, s in enumerate(old):
        if i not in used:
            changes.append({"kind": "removed", "before": describe_step(s), "after": None})
    return changes


def same_steps(a, b):
    return [_key(s) for s in a or []] == [_key(s) for s in b or []]


def ensure_joins(steps, ids=None):
    """Insert a clinical/survival join just before the first filter that needs it, keeping the user's order.
    `ids` (optional) is a parallel list of client ids; inserted joins get None. Returns (steps, ids)."""
    ids = list(ids) if ids is not None else [None] * len(steps)
    out, out_ids, joined = [], [], set()
    for s, i in zip(steps, ids):
        if s.get("op") == "join":
            joined.add(s.get("table"))
        need = s.get("table") if s.get("op") in ("filter", "any_of") else None
        if s.get("op") == "one_per_patient" and s.get("rule") in ("earliest", "latest"):
            need = "clinical"                     # the collection day lives in the clinical table
        if need in _AUTO_JOINS and need not in joined:
            out.append({"op": "join", "table": need, "key": "sample_id", "mode": "rows"})
            out_ids.append(None)
            joined.add(need)
        out.append(s)
        out_ids.append(i)
    return out, out_ids
