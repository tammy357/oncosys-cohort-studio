"""Advanced search: a structured form turned into exact steps (no model involved), checked like AI steps."""
from cohort_studio import ops
from cohort_studio.assistant.verify import Checker, canonicalize


class FormError(ValueError):
    pass


def _list(form, key):
    v = form.get(key) or []
    return [x for x in (v if isinstance(v, list) else [v]) if x not in (None, "")]


def form_options(catalog):
    """Everything the advanced form offers, with real counts."""
    p = lambda f: catalog.profile("clinical", f)  # noqa: E731

    def values(field):
        prof = p(field)
        return [{"value": v["value"], "count": v["count"]} for v in (prof or {}).get("values", [])]

    stage = p("stage")
    groups = {}
    for v in (stage or {}).get("values", []):
        g = ops.stage_group(v["value"])
        if g:
            groups[g] = groups.get(g, 0) + v["count"]
    extra = []
    for (table, field), prof in catalog.profiles.items():
        if table == "clinical" and field not in ("er_status", "pr_status", "her2_status", "pam50", "stage", "age",
                                                 "histology", "gender", "menopause"):
            extra.append({"field": field, "label": catalog.label(table, field), "numeric": prof["numeric"],
                          "values": [{"value": v["value"], "count": v["count"]} for v in prof["values"][:40]]})
    return {
        "sample_types": [{"value": t, "count": catalog.sample_type_counts.get(t, 0)} for t in ops.SAMPLE_TYPES],
        "layers": [{"value": k, "count": n} for k, n in catalog.layer_counts.items()],
        "receptors": {k: values(f) for k, f in (("er", "er_status"), ("pr", "pr_status"), ("her2", "her2_status"))},
        "pam50": values("pam50"),
        "stage_groups": [{"value": g, "count": groups[g]} for g in ("I", "II", "III", "IV") if g in groups],
        "stages": values("stage"),
        "histology": values("histology"),
        "menopause": values("menopause"),
        "gender": values("gender"),
        "age": (p("age") or {}).get("numeric"),
        "genes": [{"value": g, "count": n} for g, n in catalog.gene_counts.items()],
        "slide_kinds": [{"value": k, "count": n} for k, n in catalog.slide_sample_counts.items()],
        "survival_endpoints": [{"value": "pfi", "label": "Progression-free interval (recommended for BRCA)"},
                               {"value": "os", "label": "Overall survival"},
                               {"value": "dss", "label": "Disease-specific survival"},
                               {"value": "dfi", "label": "Disease-free interval"}],
        "extra_fields": extra,
    }


def build_spec(form, store, catalog):
    """Return (spec, corrections). Raises FormError when something can't run on this release."""
    steps, said = [], []
    types = _list(form, "sample_types") or ["Primary Tumor"]
    steps.append({"op": "sample_type", "values": types})
    said.append(" + ".join(t.lower() for t in types))
    for layer in _list(form, "require_layers"):
        steps.append({"op": "layer", "layer": layer})
        said.append(f"with {layer}")
    for layer in _list(form, "exclude_layers"):
        steps.append({"op": "exclude_layer", "layer": layer})
        said.append(f"without {layer}")
    for gene in _list(form, "genes_mutated"):
        steps.append({"op": "mutation", "gene": gene.upper(), "mutated": True})
        said.append(f"{gene.upper()} mutated")
    for gene in _list(form, "genes_not_mutated"):
        steps.append({"op": "mutation", "gene": gene.upper(), "mutated": False})
        said.append(f"{gene.upper()} not mutated")

    def clin(field, cmp, value, words):
        steps.append({"op": "filter", "table": "clinical", "field": field, "cmp": cmp, "value": value})
        said.append(words)

    if form.get("hr_positive"):
        steps.append({"op": "any_of", "table": "clinical", "conditions": [
            {"field": "er_status", "cmp": "in", "value": ["Positive"]},
            {"field": "pr_status", "cmp": "in", "value": ["Positive"]}]})
        said.append("ER+ or PR+")
    for key, field in (("er", "er_status"), ("pr", "pr_status"), ("her2", "her2_status")):
        v = form.get(key)
        if v and v != "any":
            clin(field, "in", [v], f"{key.upper()} {v}")
    if _list(form, "pam50"):
        clin("pam50", "in", _list(form, "pam50"), "PAM50 " + "/".join(_list(form, "pam50")))
    groups, exact = _list(form, "stage_groups"), _list(form, "stages")
    if exact:
        clin("stage", "in", exact + [s["value"] for s in (catalog.profile("clinical", "stage") or {}).get("values", [])
                                     if ops.stage_group(s["value"]) in groups and s["value"] not in exact],
             "stage " + ", ".join(exact + groups))
    elif groups:
        clin("stage", "stage_in", groups, "stage " + "/".join(groups))
    lo, hi = form.get("age_min"), form.get("age_max")
    if lo not in (None, "") and hi not in (None, ""):
        clin("age", "between", [int(lo), int(hi)], f"age {lo}-{hi}")
    elif lo not in (None, ""):
        clin("age", "gte", int(lo), f"age >= {lo}")
    elif hi not in (None, ""):
        clin("age", "lte", int(hi), f"age <= {hi}")
    for key, field in (("histology", "histology"), ("menopause", "menopause")):
        if _list(form, key):
            clin(field, "in", _list(form, key), f"{key}: " + "; ".join(_list(form, key)))
    if form.get("gender") and form["gender"] != "any":
        clin("gender", "in", [form["gender"]], form["gender"].lower())
    if form.get("radiation") in ("YES", "NO"):
        clin("radiation_therapy", "in", [form["radiation"]], f"radiation therapy {form['radiation'].lower()}")
    if form.get("neoadjuvant") in ("Yes", "No"):
        clin("history_of_neoadjuvant_treatment", "in", [form["neoadjuvant"]], f"neoadjuvant {form['neoadjuvant'].lower()}")
    if form.get("lymph_nodes") in ("positive", "negative"):
        prof = catalog.profile("clinical", "pathologic_N")
        prefixes = ("n1", "n2", "n3") if form["lymph_nodes"] == "positive" else ("n0",)
        vals = [v["value"] for v in (prof or {}).get("values", []) if v["value"].lower().startswith(prefixes)]
        if not vals:
            raise FormError("This release has no lymph-node category values.")
        clin("pathologic_N", "in", vals, f"lymph node {form['lymph_nodes']}")
    for f in form.get("extra_filters") or []:
        if f.get("field") and f.get("cmp"):
            clin(f["field"], f["cmp"], f.get("value"), f"{f['field']} {f['cmp']} {f.get('value')}")
    endpoint = form.get("survival_endpoint")
    if endpoint:
        steps.append({"op": "filter", "table": "survival", "field": endpoint,
                      "cmp": "is_event" if form.get("survival_event_only") else "notnull", "value": None})
        said.append(f"{endpoint.upper()} {'event' if form.get('survival_event_only') else 'recorded'}")
    years = form.get("min_followup_years")
    if years not in (None, "", 0, "0"):
        time_field = "pfi_time" if endpoint == "pfi" else "os_time"
        steps.append({"op": "filter", "table": "survival", "field": time_field, "cmp": "gte",
                      "value": int(round(float(years) * 365.25))})
        said.append(f"follow-up >= {years} years")
    pairs = form.get("normals")
    if pairs in ("pairs", "has_normal"):
        step = {"op": "join", "table": "normals", "key": "patient_id", "mode": "rows", "add_to_cohort": pairs == "pairs"}
        if _list(form, "normal_layers"):
            step["require_layers"] = _list(form, "normal_layers")
        steps.append(step)
        said.append("tumour-normal pairs" if pairs == "pairs" else "has a normal sample")
    slides = form.get("slides")
    if slides in ("diagnostic", "frozen", "any"):
        kinds = {"diagnostic": ["diagnostic (FFPE)"], "frozen": ops.FROZEN_KINDS, "any": []}[slides]
        steps.append({"op": "join", "table": "slides", "key": "sample_id",
                      "mode": "exists" if form.get("slides_once") else "rows", "slide_kinds": kinds})
        said.append(f"{slides} slide")
    if form.get("one_per_patient"):
        steps.append({"op": "one_per_patient", "rule": "priority"})
        said.append("one sample per patient")

    chk = Checker(store, catalog, allow_questions=False)
    checked = chk.check_steps(steps)
    if chk.unsupported:
        raise FormError("; ".join(f"{u['phrase']}: {u['reason']}" for u in chk.unsupported))
    checked, _ = canonicalize(checked)
    spec = {"request": "Advanced search: " + ", ".join(said), "steps": checked, "notes": [], "unparsed": [],
            "unparsed_confirmed": False, "parser": "advanced"}
    return spec, chk.corrections
