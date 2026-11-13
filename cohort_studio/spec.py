"""The cohort spec: the editable list of steps between a plain-English request and the data.

A spec is plain JSON, so the AI, the rule-based parser, the app's editor and the exported
script all speak the same language. Step types:

  {"op": "sample_type", "values": ["Primary Tumor"]}
  {"op": "layer", "layer": "rna"}                       # rna, protein, mutation, copy_number, survival, clinical
  {"op": "join", "table": "slides", "key": "sample_id", "mode": "rows", "slide_kinds": ["diagnostic (FFPE)"]}
  {"op": "join", "table": "clinical", "key": "sample_id", "mode": "rows"}
  {"op": "join", "table": "normals", "key": "patient_id", "mode": "exists", "add_to_cohort": false}
  {"op": "filter", "table": "clinical", "field": "er_status", "cmp": "in", "value": ["Positive"]}
  {"op": "mutation", "gene": "TP53", "mutated": true}
  {"op": "dedupe"}                                      # keep one row per sample
  {"op": "one_per_patient", "rule": "priority", "order": ["Primary Tumor", "Metastatic", "Solid Tissue Normal"]}
  {"op": "one_per_patient", "rule": "earliest"}         # or "latest"; needs sample collection days in clinical
  {"op": "where", "condition": {"any": [{"table": "clinical", "field": "er_status", "cmp": "in", "value": ["Negative"]},
                                         {"gene": "TP53", "mutated": true}]}}
      # AND / OR / NOT across layers, sample types, genes, slides and clinical/survival fields; each sample is
      # looked up by its own ID (no join, no inflation). "apply_to": "both" after a tumour-normal pairs join makes
      # the condition hold for the tumour and its normal ("added_normals": the normal only).

join modes: "rows" = ordinary join (one row per match, can inflate counts);
            "exists" = keep each sample once if it has at least one match.
"""
import copy
import json

from . import ops
from .data import TABLE_FIELDS

JOIN_TABLES = {
    "slides": {"label": "pathology slides", "keys": ["sample_id", "patient_id"]},
    "clinical": {"label": "clinical records", "keys": ["sample_id", "patient_id"]},
    "survival": {"label": "survival records", "keys": ["sample_id", "patient_id"]},
    "normals": {"label": "normal-tissue samples from the same patient", "keys": ["patient_id"]},
}
LAYER_LABELS = {"rna": "RNA expression", "protein": "protein (RPPA)", "mutation": "mutation",
                "copy_number": "copy-number", "survival": "curated survival", "clinical": "clinical"}
CMP_WORDS = {"in": "is one of", "not_in": "is not", "notnull": "is recorded", "gte": "is at least",
             "lte": "is at most", "between": "is between", "stage_in": "is stage", "is_event": "had the event",
             "contains": "contains", "in_raw": "is exactly"}


def new_spec(request="", steps=None, parser="manual"):
    return {"request": request, "steps": steps or [], "notes": [], "unparsed": [],
            "unparsed_confirmed": False, "parser": parser}


def clone(spec):
    return copy.deepcopy(spec)


def to_json(spec):
    return json.dumps(spec, indent=2)


# ---------- condition trees (the "where" step) ----------
GROUP_KEYS = ("all", "any")
TEST_KINDS = ("layer", "sample_type", "gene", "slide_kinds", "table")
MAX_CONDITION_DEPTH = 8
MAX_CONDITION_TESTS = 40


def condition_tests(node):
    """Every test (leaf) of a condition tree, depth first."""
    if not isinstance(node, dict):
        return []
    for key in GROUP_KEYS:
        if key in node:
            return [t for k in node[key] for t in condition_tests(k)]
    if "not" in node:
        return condition_tests(node["not"])
    return [node]


def resolve_condition(node, store):
    """A copy of the tree with each clinical/survival test's real column name added (for the engine and scripts)."""
    for key in GROUP_KEYS:
        if key in node:
            return {key: [resolve_condition(k, store) for k in node[key]]}
    if "not" in node:
        return {"not": resolve_condition(node["not"], store)}
    if "table" in node:
        return {**node, "column": store.resolve_field(node["table"], node["field"])}
    return dict(node)


def condition_sources(node):
    """The tables a condition reads: manifest, slides, mutation, clinical, survival."""
    need = set()
    for t in condition_tests(node):
        if "layer" in t or "sample_type" in t:
            need.add("manifest")
        elif "gene" in t:
            need.add("mutation")
        elif "slide_kinds" in t:
            need.add("slides")
        elif "table" in t:
            need.add(t["table"])
    return need


def _describe_test(t):
    if "layer" in t:
        return f"has {LAYER_LABELS.get(t['layer'], t['layer'])} data"
    if "sample_type" in t:
        return "is a " + " or ".join(v.lower() for v in t["sample_type"]) + " sample"
    if "gene" in t:
        return f"{t['gene']} {'mutated' if t.get('mutated', True) else 'not mutated'}"
    if "slide_kinds" in t:
        kinds = t["slide_kinds"] or []
        what = ("frozen-section" if set(kinds) == set(ops.FROZEN_KINDS) else
                "pathology" if not kinds or set(kinds) == set(ops.SLIDE_KINDS) else " or ".join(kinds))
        return f"has a {what} slide"
    label = TABLE_FIELDS.get(t.get("table"), {}).get(t.get("field"), {}).get("label", t.get("field"))
    value = t.get("value")
    if isinstance(value, list):
        value = (" and " if t.get("cmp") == "between" else " or ").join(str(v) for v in value)
    words = CMP_WORDS.get(t.get("cmp"), t.get("cmp"))
    return f"{label} {words}" + (f" {value}" if value not in (None, "") else "")


def describe_condition(node):
    """A condition in plain words, with AND / OR / NOT in capitals and brackets around nested groups."""
    if not isinstance(node, dict) or any(k in node and not isinstance(node[k], list) for k in GROUP_KEYS):
        return json.dumps(node, default=str)[:120]   # malformed: shown as written (validation explains what's wrong)
    for key in GROUP_KEYS:
        if key in node:
            parts = []
            for k in node[key]:
                text = describe_condition(k)
                nested = isinstance(k, dict) and any(isinstance(k.get(g), list) and len(k[g]) > 1 for g in GROUP_KEYS)
                parts.append(f"({text})" if nested else text)
            return (" AND " if key == "all" else " OR ").join(parts)
    if "not" in node:
        return f"NOT ({describe_condition(node['not'])})"
    if sum(k in node for k in TEST_KINDS) != 1:
        return json.dumps(node, default=str)[:120]
    try:
        return _describe_test(node)
    except (TypeError, AttributeError):            # wrong value types (e.g. a list as a layer): validation says why
        return json.dumps(node, default=str)[:120]


def check_condition(node, store, depth=0):
    """Problems with a condition tree (an empty list means it can run on this release)."""
    if not isinstance(node, dict) or not node:
        return ["A condition must be a test or a group ('all', 'any', 'not')."]
    if depth > MAX_CONDITION_DEPTH:
        return [f"The condition is nested more than {MAX_CONDITION_DEPTH} levels deep."]
    groups = [k for k in (*GROUP_KEYS, "not") if k in node]
    if groups:
        if len(node) != 1:
            return [f"A group must be exactly one of all / any / not, with nothing beside it: {json.dumps(node)[:80]}."]
        key = groups[0]
        if key == "not":
            return check_condition(node["not"], store, depth + 1)
        kids = node[key]
        if not isinstance(kids, list) or not kids:
            return [f"'{key}' needs a list of conditions."]
        return [p for k in kids for p in check_condition(k, store, depth + 1)]
    kinds = [k for k in TEST_KINDS if k in node]
    if len(kinds) != 1:
        return [f"A test must be exactly one of {', '.join(TEST_KINDS)}: {json.dumps(node)[:80]}."]
    if kinds[0] in ("sample_type", "slide_kinds") and not (isinstance(node[kinds[0]], list)
                                                          and all(isinstance(v, str) for v in node[kinds[0]])):
        return [f"'{kinds[0]}' needs a list of names, e.g. {kinds[0]}: [...]."]
    if kinds[0] in ("layer", "gene") and not isinstance(node[kinds[0]], str):
        return [f"'{kinds[0]}' needs one name, not {json.dumps(node[kinds[0]])[:40]}."]
    if kinds[0] == "gene" and not isinstance(node.get("mutated", True), bool):
        return ["'mutated' must be true or false."]
    if kinds[0] == "table":
        cmp, value = node.get("cmp"), node.get("value")
        if not all(isinstance(node.get(k), str) for k in ("table", "field", "cmp")):
            return [f"A field test needs table, field and cmp as names: {json.dumps(node)[:80]}."]
        number = (int, float)
        if cmp in ("in", "not_in", "in_raw", "stage_in", "contains") and not (isinstance(value, list) and value):
            return [f"'{cmp}' needs a list of values, e.g. [\"Negative\"], not {json.dumps(value)[:40]}."]
        if cmp in ("gte", "lte") and (isinstance(value, bool) or not isinstance(value, number)):
            return [f"'{cmp}' needs a number, not {json.dumps(value)[:40]}."]
        if cmp == "between" and not (isinstance(value, list) and len(value) == 2
                                     and all(isinstance(v, number) and not isinstance(v, bool) for v in value)):
            return ["'between' needs [low, high] as numbers."]
    if "layer" in node:
        return [] if node["layer"] in ops.LAYER_COLUMNS else [f"Unknown data layer '{node['layer']}'."]
    if "sample_type" in node:
        values = node["sample_type"] or []
        bad = [v for v in values if v not in ops.SAMPLE_TYPES]
        return [f"Unknown sample type {bad}. Use {ops.SAMPLE_TYPES}."] if bad or not values else []
    if "gene" in node:
        if not store.available("mutation"):
            return ["Mutation file isn't available: run python data/tcga-brca/fetch.py"]
        return [] if node["gene"] in store.genes() else [f"Gene '{node['gene']}' isn't in the mutation file."]
    if "slide_kinds" in node:
        bad = [k for k in node["slide_kinds"] or [] if k not in ops.SLIDE_KINDS]
        return [f"Unknown slide kind {bad}."] if bad else []
    if "table" in node:
        table = node["table"]
        if table not in ("clinical", "survival"):
            return [f"A condition can test clinical or survival fields, not '{table}'."]
        if not store.available(table):
            return [f"{table} file isn't available: run python data/tcga-brca/fetch.py"]
        problems = []
        if store.resolve_field(table, node.get("field")) is None:
            problems.append(f"This release has no column for '{node.get('field')}' in {table}.")
        if node.get("cmp") not in CMP_WORDS:
            problems.append(f"Unknown comparison '{node.get('cmp')}'.")
        if node.get("cmp") == "between" and not (isinstance(node.get("value"), list) and len(node["value"]) == 2):
            problems.append("'between' needs [low, high].")
        return problems
    return [f"Unknown test {json.dumps(node)[:80]}."]


def describe_step(step, store=None):
    """One step in plain words."""
    op = step.get("op")
    if op == "sample_type":
        return "Keep " + " and ".join(step["values"]).lower() + " samples"
    if op == "layer":
        return f"Require {LAYER_LABELS.get(step['layer'], step['layer'])} data"
    if op == "join":
        t = JOIN_TABLES.get(step["table"], {"label": step["table"]})["label"]
        if step["table"] == "slides" and step.get("slide_kinds"):
            kinds = step["slide_kinds"]
            t = ("diagnostic (FFPE) slides" if kinds == ["diagnostic (FFPE)"] else
                 "pathology slides of any kind" if set(kinds) == set(ops.SLIDE_KINDS) else
                 "frozen-section slides" if set(kinds) == set(ops.FROZEN_KINDS) else ", ".join(kinds) + " slides")
        how = ("one row per match" if step.get("mode", "rows") == "rows"
               else "keep each sample once if it has at least one match")
        extra = "; add those samples to the cohort" if step.get("add_to_cohort") else ""
        if step["table"] == "normals" and step.get("require_layers"):
            extra += "; the normal sample must have " + " and ".join(
                LAYER_LABELS.get(x, x) for x in step["require_layers"]) + " data"
        return f"Join {t} on {step.get('key', 'sample_id')} ({how}{extra})"
    if op == "exclude_layer":
        return f"Exclude samples that have {LAYER_LABELS.get(step['layer'], step['layer'])} data"
    if op == "any_of":
        parts = []
        for c in step.get("conditions", []):
            label = TABLE_FIELDS.get(step.get("table"), {}).get(c.get("field"), {}).get("label", c.get("field"))
            value = c.get("value")
            if isinstance(value, list):
                value = " or ".join(str(v) for v in value)
            words = CMP_WORDS.get(c.get("cmp"), c.get("cmp"))
            parts.append(f"{label} {words}" + (f" {value}" if value not in (None, "") else ""))
        return "Keep samples where " + ", OR ".join(parts)
    if op == "filter":
        label = TABLE_FIELDS.get(step["table"], {}).get(step["field"], {}).get("label", step["field"])
        value = step.get("value")
        if isinstance(value, list):
            value = (" and " if step["cmp"] == "between" else " or ").join(str(v) for v in value)
        words = CMP_WORDS.get(step["cmp"], step["cmp"])
        return f"Keep samples where {label} {words}" + (f" {value}" if value not in (None, "") else "")
    if op == "mutation":
        return f"Keep samples {'with' if step.get('mutated', True) else 'without'} a {step['gene']} mutation call"
    if op == "dedupe":
        return "Keep one row per sample"
    if op == "where":
        who = {"both": "; in each tumour-normal pair, both samples must match",
               "added_normals": "; checked on the normal sample each pair added"}.get(step.get("apply_to", "sample"), "")
        return "Keep samples where " + describe_condition(step.get("condition") or {}) + who
    if op == "one_per_patient":
        rule = step.get("rule", "priority")
        if rule in ("earliest", "latest"):
            return f"Keep one sample per patient: the {rule} collected"
        order = step.get("order") or ops.SAMPLE_TYPE_PRIORITY
        return "Keep one sample per patient: " + ", then ".join(o.lower() for o in order) + " (ties: lowest barcode)"
    return json.dumps(step)


def validate(spec, store):
    """Return a list of (step_index, message). An empty list means every step can run on this release."""
    problems = []
    joined = set()
    pairs = False          # a tumour-normal pairs join came earlier (needed by apply_to)
    for i, step in enumerate(spec.get("steps", [])):
        op = step.get("op")
        if op == "sample_type":
            bad = [v for v in step.get("values", []) if v not in ops.SAMPLE_TYPES]
            if bad or not step.get("values"):
                problems.append((i, f"Unknown sample type {bad}. Use {ops.SAMPLE_TYPES}."))
        elif op == "layer":
            if step.get("layer") not in ops.LAYER_COLUMNS:
                problems.append((i, f"Unknown data layer '{step.get('layer')}'."))
        elif op == "join":
            table = step.get("table")
            if table not in JOIN_TABLES:
                problems.append((i, f"Unknown table '{table}'."))
                continue
            if step.get("key") not in JOIN_TABLES[table]["keys"]:
                problems.append((i, f"{table} can be joined on {JOIN_TABLES[table]['keys']}, not '{step.get('key')}'."))
            if step.get("mode", "rows") not in ("rows", "exists"):
                problems.append((i, "Join mode must be 'rows' or 'exists'."))
            if table in ("clinical", "survival") and not store.available(table):
                problems.append((i, f"{table} file isn't available: run python data/tcga-brca/fetch.py"))
            bad_kinds = [k for k in step.get("slide_kinds", []) or [] if k not in ops.SLIDE_KINDS]
            if bad_kinds:
                problems.append((i, f"Unknown slide kind {bad_kinds}."))
            bad_layers = [x for x in step.get("require_layers", []) or [] if x not in ops.LAYER_COLUMNS]
            if bad_layers:
                problems.append((i, f"Unknown data layer {bad_layers}."))
            joined.add(table)
            if table == "normals" and step.get("add_to_cohort") and step.get("mode", "rows") == "rows":
                pairs = True
        elif op == "where":
            cond = step.get("condition")
            problems += [(i, msg) for msg in dict.fromkeys(check_condition(cond, store))]
            if isinstance(cond, dict) and len(condition_tests(cond)) > MAX_CONDITION_TESTS:
                problems.append((i, f"The condition has more than {MAX_CONDITION_TESTS} tests."))
            apply_to = step.get("apply_to", "sample")
            if apply_to not in ("sample", "added_normals", "both"):
                problems.append((i, "apply_to must be 'sample', 'added_normals' or 'both'."))
            elif apply_to != "sample" and not pairs:
                problems.append((i, f"apply_to '{apply_to}' needs an earlier tumour-normal pairs join "
                                    "(normals, add_to_cohort, rows mode)."))
        elif op == "filter":
            table = step.get("table")
            if table not in joined:
                problems.append((i, f"Join {table} before filtering on it."))
            elif store.available(table) and store.resolve_field(table, step.get("field")) is None:
                problems.append((i, f"This release has no column for '{step.get('field')}' in {table}."))
            if step.get("cmp") not in CMP_WORDS:
                problems.append((i, f"Unknown comparison '{step.get('cmp')}'."))
        elif op == "mutation":
            if not store.available("mutation"):
                problems.append((i, "Mutation file isn't available: run python data/tcga-brca/fetch.py"))
            elif step.get("gene") not in store.genes():
                problems.append((i, f"Gene '{step.get('gene')}' isn't in the mutation file."))
        elif op == "dedupe":
            pass
        elif op == "one_per_patient":
            rule = step.get("rule", "priority")
            if rule not in ("priority", "earliest", "latest"):
                problems.append((i, f"Unknown rule '{rule}'. Use priority, earliest or latest."))
            bad = [v for v in step.get("order") or [] if v not in ops.SAMPLE_TYPES]
            if bad:
                problems.append((i, f"Unknown sample type {bad} in the order. Use {ops.SAMPLE_TYPES}."))
            if rule in ("earliest", "latest"):
                if "clinical" not in joined:
                    problems.append((i, "Join clinical records before choosing the earliest or latest sample."))
                elif store.available("clinical") and store.resolve_field("clinical", "collection_day") is None:
                    problems.append((i, "Sample collection dates aren't recorded in this release, so the "
                                        f"{rule} sample per patient can't be chosen."))
        elif op == "exclude_layer":
            if step.get("layer") not in ops.LAYER_COLUMNS:
                problems.append((i, f"Unknown data layer '{step.get('layer')}'."))
        elif op == "any_of":
            table = step.get("table")
            conditions = step.get("conditions") or []
            if table not in joined:
                problems.append((i, f"Join {table} before filtering on it."))
            if len(conditions) < 2:
                problems.append((i, "An 'any of' step needs at least two conditions."))
            for c in conditions:
                if store.available(table) and store.resolve_field(table, c.get("field")) is None:
                    problems.append((i, f"This release has no column for '{c.get('field')}' in {table}."))
                if c.get("cmp") not in CMP_WORDS:
                    problems.append((i, f"Unknown comparison '{c.get('cmp')}'."))
        else:
            problems.append((i, f"Unknown step type '{op}'."))
    return problems
