"""Check and correct AI-drafted steps against what the release actually contains.

* A small fix with one clear answer is made and recorded (a value in the wrong case, a misspelt value,
  a field name the model mis-remembered, a single value where a list is needed).
* A fix with several plausible answers that would change who is in the cohort becomes a question.
* Anything the release can't answer becomes 'unsupported', with the reason. Nothing is dropped silently.
"""
import copy
import difflib
import json
import re

from .. import ops
from .catalog import value_flag

LIST_CMPS = {"in", "not_in", "contains", "stage_in", "in_raw"}
VALID_CMPS = LIST_CMPS | {"gte", "lte", "between", "notnull", "is_event"}
CMP_ALIASES = {"=": "in", "==": "in", "eq": "in", "equals": "in", "is": "in", "one_of": "in", "!=": "not_in",
               "ne": "not_in", "not": "not_in", "not_equal": "not_in", "none_of": "not_in", ">=": "gte", "ge": "gte",
               "min": "gte", ">": "gt", "<=": "lte", "le": "lte", "max": "lte", "<": "lt", "range": "between",
               "exists": "notnull", "not_null": "notnull", "recorded": "notnull", "event": "is_event",
               "raw_in": "in_raw", "exact": "in_raw", "includes": "contains", "like": "contains"}
OP_ALIASES = {"sampletype": "sample_type", "sample_types": "sample_type", "require_layer": "layer", "has_layer": "layer",
              "layer_absent": "exclude_layer", "exclude": "exclude_layer", "without_layer": "exclude_layer",
              "or": "any_of", "filter_any": "any_of", "anyof": "any_of", "one_sample_per_patient": "one_per_patient",
              "per_patient": "one_per_patient", "mutated": "mutation", "gene": "mutation"}
SAMPLE_TYPE_ALIASES = {"primary tumor": "Primary Tumor", "primary tumour": "Primary Tumor", "primary": "Primary Tumor",
                       "tumor": "Primary Tumor", "tumour": "Primary Tumor", "tumors": "Primary Tumor",
                       "solid tissue normal": "Solid Tissue Normal", "normal": "Solid Tissue Normal",
                       "normal tissue": "Solid Tissue Normal", "metastatic": "Metastatic", "metastasis": "Metastatic",
                       "metastases": "Metastatic"}
LAYER_ALIASES = {"rna": "rna", "rna-seq": "rna", "rnaseq": "rna", "rna_seq": "rna", "expression": "rna", "mrna": "rna",
                 "gene_expression": "rna", "protein": "protein", "rppa": "protein", "proteomics": "protein",
                 "mutation": "mutation", "mutations": "mutation", "mc3": "mutation", "copy_number": "copy_number",
                 "copy number": "copy_number", "cnv": "copy_number", "cna": "copy_number", "gistic": "copy_number",
                 "survival": "survival", "clinical": "clinical"}
TABLE_ALIASES = {"slide": "slides", "slides": "slides", "pathology_slides": "slides", "images": "slides",
                 "normal": "normals", "normals": "normals", "normal_samples": "normals", "clinical": "clinical",
                 "clinical_data": "clinical", "survival": "survival", "outcomes": "survival"}
SLIDE_ALIASES = {"diagnostic": ["diagnostic (FFPE)"], "ffpe": ["diagnostic (FFPE)"], "dx": ["diagnostic (FFPE)"],
                 "frozen": ops.FROZEN_KINDS, "frozen section": ops.FROZEN_KINDS, "any": [], "all": []}
FIELD_ALIASES = {"er": "er_status", "estrogen_receptor": "er_status", "estrogen_receptor_status": "er_status",
                 "pr": "pr_status", "progesterone_receptor": "pr_status", "progesterone_receptor_status": "pr_status",
                 "her2": "her2_status", "pam50_subtype": "pam50", "subtype": "pam50", "molecular_subtype": "pam50",
                 "pam50_call": "pam50", "ajcc_stage": "stage", "tumor_stage": "stage", "age_at_diagnosis": "age",
                 "histological_type": "histology", "histologic_type": "histology", "sex": "gender",
                 "menopause_status": "menopause", "overall_survival": "os", "overall_survival_time": "os_time",
                 "os_days": "os_time", "progression_free_interval": "pfi", "pfi_days": "pfi_time"}
GENE_ALIASES = {"HER2": "ERBB2", "HER-2": "ERBB2", "ER": "ESR1"}
ROMAN = {"1": "I", "2": "II", "3": "III", "4": "IV"}

RULE_ALIASES = {"priority": "priority", "sample_type": "priority", "type": "priority", "earliest": "earliest",
                "first": "earliest", "initial": "earliest", "latest": "latest", "last": "latest", "most recent": "latest",
                "newest": "latest"}
RANK = {"sample_type": 0, "layer": 1, "exclude_layer": 1, "mutation": 2, "one_per_patient": 9, "dedupe": 10}
TABLE_RANK = {("join", "clinical"): 3, ("filter", "clinical"): 4, ("any_of", "clinical"): 4,
              ("join", "survival"): 5, ("filter", "survival"): 6, ("any_of", "survival"): 6,
              ("join", "normals"): 7, ("join", "slides"): 8}


class _Unsupported(Exception):
    pass


def _key(step):
    return json.dumps(step, sort_keys=True)


def _as_list(value):
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


class Checker:
    """Checks one list of steps. `allow_questions=False` (inside an answer option) picks the best fix instead."""

    def __init__(self, store, catalog, allow_questions=True, id_prefix="c"):
        self.store, self.cat = store, catalog
        self.allow_questions = allow_questions
        self.corrections, self.unsupported, self.questions = [], [], []
        self._prefix = id_prefix

    # ---------- helpers ----------
    def _fix(self, frm, to, why):
        self.corrections.append({"from": str(frm), "to": str(to), "why": why, "source": "check"})

    def _ask(self, phrase, question, why, alternatives):
        """alternatives: [(label, steps)]. Returns None: the step moves into the question."""
        self.questions.append({"id": f"{self._prefix}{len(self.questions) + 1}", "phrase": phrase, "question": question,
                               "why": why, "source": "check",
                               "options": [{"label": lab, "steps": steps} for lab, steps in alternatives]})
        return None

    # ---------- steps ----------
    def check_steps(self, steps):
        out = []
        for raw in _as_list(steps):
            if not isinstance(raw, dict):
                continue
            try:
                step = self.check_step(copy.deepcopy(raw))
            except _Unsupported as exc:
                phrase, reason = exc.args
                self.unsupported.append({"phrase": phrase, "reason": reason, "alternative": None, "source": "check"})
                continue
            if step is not None:
                out.append(step)
        return out

    def check_step(self, step):
        op = str(step.get("op", "")).strip().lower()
        op = OP_ALIASES.get(op, op)
        step["op"] = op
        if op == "sample_type":
            values = []
            for v in _as_list(step.get("values")):
                canon = v if v in ops.SAMPLE_TYPES else SAMPLE_TYPE_ALIASES.get(str(v).strip().lower())
                if canon is None:
                    raise _Unsupported(str(v), f"'{v}' is not a sample type in this release ({', '.join(ops.SAMPLE_TYPES)}).")
                if canon != v:
                    self._fix(v, canon, "sample type name used in this release")
                values.append(canon)
            if not values:
                return None
            return {"op": "sample_type", "values": [t for t in ops.SAMPLE_TYPES if t in values]}
        if op in ("layer", "exclude_layer"):
            return {"op": op, "layer": self._layer(step.get("layer"))}
        if op == "mutation":
            return self._mutation(step)
        if op == "join":
            return self._join(step)
        if op == "filter":
            table, cond = self._condition(step.get("table"), step)
            if cond is None:
                return None
            return {"op": "filter", "table": table, **cond}
        if op == "any_of":
            return self._any_of(step)
        if op == "one_per_patient":
            return self._per_patient(step)
        if op == "dedupe":
            return {"op": "dedupe"}
        raise _Unsupported(op or "?", f"'{op}' is not a step this tool can run.")

    def _per_patient(self, step):
        """One sample per patient, in the engine's form: {"rule": priority|earliest|latest, "order": [...]}.
        An older form, {"prefer": [...]}, is read as a priority order."""
        raw = str(step.get("rule") or "priority").strip().lower()
        rule = RULE_ALIASES.get(raw)
        if rule is None:
            raise _Unsupported(raw, f"'{raw}' is not a way to choose one sample per patient (priority, earliest or latest).")
        if rule != raw:
            self._fix(raw, rule, "rule name used by the 'one sample per patient' step")
        if rule != "priority":
            if self.store.available("clinical") and self.store.resolve_field("clinical", "collection_day") is None:
                raise _Unsupported(raw, "Sample collection days aren't recorded in this release, so the "
                                        f"{rule} sample per patient can't be chosen. Choose by sample type instead.")
            return {"op": "one_per_patient", "rule": rule}
        order = []
        for v in _as_list(step.get("order") or step.get("prefer")) or ops.SAMPLE_TYPE_PRIORITY:
            canon = v if v in ops.SAMPLE_TYPES else SAMPLE_TYPE_ALIASES.get(str(v).strip().lower())
            if canon and canon not in order:
                order.append(canon)
        order += [t for t in ops.SAMPLE_TYPE_PRIORITY if t not in order]
        out = {"op": "one_per_patient", "rule": "priority"}
        if order != ops.SAMPLE_TYPE_PRIORITY:
            out["order"] = order
        return out

    def _layer(self, layer):
        canon = LAYER_ALIASES.get(str(layer).strip().lower())
        if canon is None:
            raise _Unsupported(str(layer), f"'{layer}' is not a data layer here ({', '.join(ops.LAYER_COLUMNS)}).")
        if canon != layer:
            self._fix(layer, canon, "data layer name used in this release")
        return canon

    def _mutation(self, step):
        raw = str(step.get("gene", "")).strip()
        gene = GENE_ALIASES.get(raw.upper(), raw.upper())
        mutated = step.get("mutated", True)
        if isinstance(mutated, str):
            mutated = mutated.strip().lower() not in ("false", "no", "not mutated", "wild-type", "wildtype", "0")
        if not self.cat.genes:
            return {"op": "mutation", "gene": gene, "mutated": bool(mutated)}
        if gene in self.cat.genes:
            if gene != raw:
                self._fix(raw, gene, "gene symbol in the mutation file")
            return {"op": "mutation", "gene": gene, "mutated": bool(mutated)}
        closest = self.cat.closest_genes(gene)
        if closest and self.allow_questions:
            word = "mutated" if mutated else "not mutated"
            return self._ask(raw, f"'{raw}' isn't in the mutation file. Which gene did you mean?",
                             "A different gene gives a different cohort.",
                             [(f"{g} {word}", [{"op": "mutation", "gene": g, "mutated": bool(mutated)}]) for g in closest])
        raise _Unsupported(raw, f"Gene '{raw}' isn't in the mutation file"
                           + (f" (closest: {', '.join(closest)})." if closest else "."))

    def _join(self, step):
        table = TABLE_ALIASES.get(str(step.get("table", "")).strip().lower())
        if table is None:
            raise _Unsupported(str(step.get("table")), f"'{step.get('table')}' is not a table this tool can join.")
        out = {"op": "join", "table": table,
               "key": step.get("key") if step.get("key") in ("sample_id", "patient_id") else
               ("patient_id" if table == "normals" else "sample_id"),
               "mode": step.get("mode") if step.get("mode") in ("rows", "exists") else "rows"}
        if table == "normals":
            out["key"] = "patient_id"
            out["add_to_cohort"] = bool(step.get("add_to_cohort", False))
            layers = [self._layer(x) for x in _as_list(step.get("require_layers"))]
            if layers:
                out["require_layers"] = layers
        if table == "slides":
            kinds = []
            for k in _as_list(step.get("slide_kinds")):
                if k in ops.SLIDE_KINDS:
                    kinds.append(k)
                elif str(k).strip().lower() in SLIDE_ALIASES:
                    mapped = SLIDE_ALIASES[str(k).strip().lower()]
                    self._fix(k, ", ".join(mapped) or "any kind", "slide kinds used in this release")
                    kinds += mapped
                else:
                    raise _Unsupported(str(k), f"'{k}' is not a slide kind here ({', '.join(ops.SLIDE_KINDS)}).")
            out["slide_kinds"] = list(dict.fromkeys(kinds))
        if table in ("clinical", "survival") and not self.store.available(table):
            raise _Unsupported(table, f"The {table} file isn't downloaded: run python data/tcga-brca/fetch.py")
        return out

    def _any_of(self, step):
        conds, table = [], None
        for c in _as_list(step.get("conditions")):
            if not isinstance(c, dict):
                continue
            t, fixed = self._condition(c.get("table") or step.get("table"), c, in_any_of=True)
            if fixed is None:
                continue
            if table and t != table:
                raise _Unsupported("or", "An 'or' across the clinical and survival tables can't be expressed.")
            table = t
            conds.append(fixed)
        if not conds:
            return None
        if len(conds) == 1:
            self._fix("any of one condition", "a single filter", "only one condition was left")
            return {"op": "filter", "table": table, **conds[0]}
        return {"op": "any_of", "table": table, "conditions": conds}

    # ---------- one condition: field, comparison, values ----------
    def _condition(self, table, cond, in_any_of=False):
        table = TABLE_ALIASES.get(str(table or "").strip().lower(), "clinical")
        field = str(cond.get("field", "")).strip()
        cmp = str(cond.get("cmp", "in")).strip().lower()
        cmp = CMP_ALIASES.get(cmp, cmp)
        value = cond.get("value")
        if not self.store.available(table):
            raise _Unsupported(field, f"The {table} file isn't downloaded: run python data/tcga-brca/fetch.py")
        table, field = self._field(table, field)
        if cmp in ("gt", "lt"):
            v = float(value)
            new = (v + 1 if cmp == "gt" else v - 1) if v.is_integer() else v
            new = int(new) if float(new).is_integer() else new
            self._fix(f"{'>' if cmp == 'gt' else '<'} {value:g}" if isinstance(value, (int, float)) else value,
                      f"{'≥' if cmp == 'gt' else '≤'} {new}", "whole-number comparison")
            cmp, value = ("gte" if cmp == "gt" else "lte"), new
        if cmp not in VALID_CMPS:
            raise _Unsupported(field, f"'{cmp}' is not a comparison this tool supports.")
        if cmp in LIST_CMPS and not isinstance(value, list):
            value = _as_list(value)
        if cmp in ("notnull", "is_event"):
            value = None
        prof = self.cat.profile(table, field)
        if cmp in ("gte", "lte", "between"):
            value = self._numbers(table, field, cmp, value)
            if value is None:
                return table, None
            return table, {"field": field, "cmp": cmp, "value": value}
        if prof is not None and not prof["numeric"] and cmp in LIST_CMPS:
            if field == "stage" or self.store.resolve_field(table, field) in ("pathologic_stage",):
                cmp, value = self._stage(cmp, value, prof)
            elif cmp == "contains":
                cmp, value = self._contains(field, value, prof)
            else:
                res = self._values(table, field, cmp, value, prof, in_any_of)
                if res is None:
                    return table, None
                cmp, value = res
        return table, {"field": field, "cmp": cmp, "value": value}

    def _field(self, table, field):
        alias = FIELD_ALIASES.get(field.lower())
        if alias:
            target = "survival" if alias in ("os", "os_time", "pfi", "pfi_time", "dss", "dfi") else "clinical"
            if alias != field or target != table:
                self._fix(field, alias, "field name used in this release")
            return target, alias
        if self.store.resolve_field(table, field):
            friendly = self.cat.friendly(table, field)
            if friendly != field:
                self._fix(field, friendly, "same column, named as the tool names it")
            return table, friendly
        other = "survival" if table == "clinical" else "clinical"
        if self.store.available(other) and self.store.resolve_field(other, field):
            self._fix(f"{table}.{field}", f"{other}.{field}", "this field is in the other table")
            return other, self.cat.friendly(other, field)
        ranked = self.cat.best_fields(table, field)
        if ranked and ranked[0][0] >= 0.8 and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 0.08):
            self._fix(field, ranked[0][1], "closest field in this release")
            return table, ranked[0][1]
        names = ", ".join(f for score, f in ranked[:3] if score >= 0.6)
        raise _Unsupported(field, f"This release has no '{field}' field" + (f" (closest: {names})." if names else "."))

    def _numbers(self, table, field, cmp, value):
        try:
            nums = [float(v) for v in _as_list(value)] if cmp == "between" else [float(value)]
        except (TypeError, ValueError):
            raise _Unsupported(field, f"'{value}' is not a number.")
        if cmp == "between":
            if len(nums) != 2:
                raise _Unsupported(field, "'between' needs a low and a high value.")
            nums = sorted(nums)
        clean = [int(n) if n.is_integer() else n for n in nums]
        if field == "age" and any(n < 0 or n > 120 for n in clean):
            raise _Unsupported(field, f"Age {clean} is outside 0-120.")
        if table == "survival" and field.endswith("_time") and all(0 < n <= 30 for n in clean):
            as_days = [int(round(n * 365.25)) for n in clean]
            if self.allow_questions:
                label = "the follow-up time"
                fmt = (lambda xs: " to ".join(map(str, xs)))
                return self._ask(f"{fmt(clean)}", f"Is {label} {fmt(clean)} years or {fmt(clean)} days?",
                                 "Survival times are stored in days, so the unit changes the cohort completely.",
                                 [(f"{fmt(clean)} years ({fmt(as_days)} days)",
                                   [{"op": "filter", "table": table, "field": field, "cmp": cmp,
                                     "value": as_days if cmp == "between" else as_days[0]}]),
                                  (f"{fmt(clean)} days", [{"op": "filter", "table": table, "field": field, "cmp": cmp,
                                                         "value": clean if cmp == "between" else clean[0]}])])
            self._fix(f"{clean} (years)", f"{as_days} days", "survival times are stored in days")
            clean = as_days
        return clean if cmp == "between" else clean[0]

    def _stage(self, cmp, values, prof):
        exact = [v["value"] for v in prof["values"]]
        groups, specific = [], []
        for v in values:
            text = re.sub(r"^\s*stage\s*", "", str(v), flags=re.I).strip().upper()
            text = ROMAN.get(text, text)
            if text in ("I", "II", "III", "IV"):
                groups.append(text)
            elif f"Stage {text}" in exact:
                specific.append(f"Stage {text}")
            else:
                raise _Unsupported(str(v), f"'{v}' is not a stage in this release ({', '.join(exact)}).")
        if not specific and cmp in ("stage_in", "in"):
            if cmp == "in":
                self._fix("stage in " + ", ".join(values), "stage group " + ", ".join(groups),
                          "whole stage groups include their sub-stages")
            return "stage_in", groups
        expanded = specific + [e for e in exact if ops.stage_group(e) in groups]
        if cmp == "stage_in":
            self._fix("stage_in " + ", ".join(map(str, values)), "exact stages " + ", ".join(expanded),
                      "sub-stages need exact values")
            return "in", expanded
        return cmp, expanded

    def _contains(self, field, values, prof):
        actual = [v["value"] for v in prof["values"]]
        chosen = []
        for sub in values:
            s = str(sub).strip().lower()
            starts = [a for a in actual if a.lower().startswith(s)]
            inside = [a for a in actual if s in a.lower()]
            hits = starts or inside
            if not hits:
                raise _Unsupported(str(sub), f"No {field} value contains '{sub}' (values: {', '.join(actual[:8])}).")
            chosen += hits
        chosen = list(dict.fromkeys(chosen))
        self._fix(f"{field} contains " + ", ".join(map(str, values)), "is one of " + "; ".join(chosen),
                  "made the matched values explicit")
        return "in", chosen

    def _values(self, table, field, cmp, values, prof, in_any_of):
        actual = [v["value"] for v in prof["values"]]
        by_lower = {a.lower(): a for a in actual}
        fixed, star = [], False
        for v in values:
            sv = str(v).strip()
            if sv.lower() in by_lower:
                canon = by_lower[sv.lower()]
                if canon != sv:
                    self._fix(sv, canon, "value as written in this release")
            else:
                starts = [a for a in actual if a.lower().startswith(sv.lower())]
                close = [by_lower[c] for c in difflib.get_close_matches(sv.lower(), list(by_lower), n=3, cutoff=0.75)]
                inside = [a for a in actual if sv.lower() in a.lower()]
                cands = list(dict.fromkeys(starts or close or inside))
                if not cands:
                    raise _Unsupported(sv, f"'{sv}' is not a value of {field} (values: {', '.join(actual[:10])}).")
                if len(cands) > 1 and self.allow_questions and not in_any_of:
                    others = [x for x in fixed]
                    alts = [(f"{field} {('is' if cmp != 'not_in' else 'is not')} {c}",
                             [{"op": "filter", "table": table, "field": field, "cmp": cmp, "value": others + [c]}])
                            for c in cands]
                    alts.append((f"any of: {', '.join(cands)}",
                                 [{"op": "filter", "table": table, "field": field, "cmp": cmp, "value": others + cands}]))
                    return self._ask(sv, f"'{sv}' matches several {field} values. Which do you mean?",
                                     "Each value selects different samples.", alts)
                canon = cands[0]
                self._fix(sv, canon, "closest value in this release")
            fixed.append(canon)
            star = star or value_flag(canon) == "*"
        if star and cmp == "in":
            self._fix("in", "in_raw", f"'{', '.join(x for x in fixed if value_flag(x) == '*')}' is otherwise treated as "
                                      "not recorded")
            cmp = "in_raw"
        return cmp, list(dict.fromkeys(fixed))


# ---------- assembling steps ----------
def merge(base, extra):
    """Add option steps to base steps: a sample-type, a per-patient rule or a join on the same table replaces the base one."""
    out = [copy.deepcopy(s) for s in base]
    for s in extra:
        s = copy.deepcopy(s)
        if s["op"] in ("sample_type", "one_per_patient"):
            out = [x for x in out if x["op"] != s["op"]]
        elif s["op"] == "join":
            out = [x for x in out if not (x["op"] == "join" and x.get("table") == s.get("table"))]
        if _key(s) not in {_key(x) for x in out}:
            out.append(s)
    return out


def canonicalize(steps):
    """Sort into the engine's reading order, add a sample-type step and any join a filter needs. Returns (steps, notes)."""
    notes = []
    steps = [s for s in steps if s]
    seen, uniq = set(), []
    for s in steps:
        if _key(s) not in seen:
            seen.add(_key(s))
            uniq.append(s)
    steps = uniq
    if not any(s["op"] == "sample_type" for s in steps):
        steps.insert(0, {"op": "sample_type", "values": ["Primary Tumor"]})
        notes.append({"from": "(no sample type)", "to": "Primary Tumor",
                      "why": "no sample type was given, so the cohort starts from primary tumours", "source": "check"})
    for table in ("clinical", "survival"):
        needs = any(s["op"] in ("filter", "any_of") and s.get("table") == table for s in steps)
        # choosing the earliest / latest sample reads the collection day from the clinical table
        needs = needs or (table == "clinical" and any(s["op"] == "one_per_patient" and s.get("rule") in ("earliest", "latest")
                                                      for s in steps))
        if needs and not any(s["op"] == "join" and s.get("table") == table for s in steps):
            steps.append({"op": "join", "table": table, "key": "sample_id", "mode": "rows"})

    def rank(s):
        return RANK.get(s["op"], TABLE_RANK.get((s["op"], s.get("table")), 5))
    return sorted(steps, key=rank), notes
