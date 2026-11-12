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
