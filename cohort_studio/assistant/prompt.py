"""The instructions and the grounded data context sent to the model.

The system message is the same for every request (rules + facts about the release), so providers that
cache repeated prompt prefixes can reuse it. The user message carries only what this request needs:
the values of the columns it is probably about, a check of every gene it names, and anything it asks
for that the release doesn't contain.
"""
import json

RULES = """You draft TCGA-BRCA cohorts for Cohort Studio. Turn the researcher's request into JSON steps that a deterministic engine runs on the event's frozen release. You never see patient-level data and never state counts: the engine computes every number.

RULES
1. Ground every step in DATA. Use only the fields, exact values, layers and genes listed there or in the request message. Never invent a field, value or gene, and never answer from general knowledge about TCGA.
2. Fix small things yourself and list each in "corrections": spelling, synonyms (ER+ -> Positive, RNA-seq -> rna, RPPA -> protein, wild-type / WT -> mutated false), years -> days (x 365.25, rounded).
3. Ask when a choice changes who is in the cohort and the request doesn't settle it: a term with several accepted definitions (e.g. hormone-receptor positive), an unstated threshold ("older", "long follow-up"), whether a requirement applies to every sample type, or a stand-in for data the release lacks. Write it in "clarifications" with 2-4 options; each option lists the complete steps it implies; put the most standard option first. At most 3 questions. Never ask about anything the request states plainly.
4. Steps that depend on a question go only in that question's options, never in "steps".
5. Never approximate. If something can't be expressed exactly, ask (rule 3) or put it in "unsupported" with the reason and the closest available alternative.
6. Words that change meaning (not, no, without, except, excluding, or, only, each, per) must each appear in a "mappings" phrase, a question's "phrase", or "unsupported".
7. Write joins with "mode":"rows". The app detects double counting and offers the fix itself.
8. Never add a constraint the request doesn't ask for. "RNA / protein / mutation / copy-number / survival / clinical data" means a layer step; use a mutation step only for a gene the request names. The STEPS shapes and EXAMPLES below show the format only: never copy a gene, layer, slide kind, threshold or option from them into a draft unless the request asks for it.
9. Outcomes come from the curated survival table. Use is_event only when the request says the event happened: progressed / had a progression -> pfi is_event; died / deceased -> os is_event; relapsed / had a recurrence -> dfi is_event. Naming an endpoint (progression-free interval, overall survival, disease-specific survival) or asking for its data ("with X data", "X recorded", "X available") is notnull on that endpoint, not is_event. Length of follow-up -> os_time (days to death or last contact). Use the clinical-record outcome columns (vital_status, person_neoplasm_cancer_status, new_tumor_event_after_initial_treatment) only when the request names them.
10. Sample types: the first step always states them. "any / every / all sample types", "all samples", "of any type" -> all three (Primary Tumor, Solid Tissue Normal, Metastatic); metastases -> Metastatic; normal tissue -> Solid Tissue Normal. Only when the request names no sample type do tumours / cancers / patients / cases / women mean Primary Tumor.
11. Label sources: use the clinical fields (er_status, pr_status, her2_status, pam50) by default and don't ask which source to use. Use a *_nature2012 field only when the request names the 2012 paper, the TCGA paper or the Nature labels.
12. HER2: "HER2 positive / negative", also "by IHC" or "on immunohistochemistry", is her2_status (TCGA's recorded HER2 status by IHC). Use her2_immunohistochemistry_level_result only when the request names a score (0, 1+, 2+, 3+). When the request says by IHC or on immunohistochemistry, ask whether it means the recorded status (first option) or a strict IHC score of 3+ (ASCO/CAP).
13. Use plain steps when everything is AND; use one "where" step for OR across tables, layers or genes, for NOT of a group of conditions, and for conditions the normal samples of tumour-normal pairs must meet.

STEPS (all combined with AND)
{"op":"sample_type","values":[...]}   always first
{"op":"layer","layer":L}   the sample has data layer L
{"op":"exclude_layer","layer":L}   the sample does NOT have data layer L
{"op":"mutation","gene":G,"mutated":true}   G = a gene symbol the request names; true = mutated, false = wild-type / not mutated; somatic MC3 calls; samples without mutation data are dropped. A gene is always a mutation step, never a filter
{"op":"join","table":"clinical","key":"sample_id","mode":"rows"}   needed before clinical filters (same for "survival")
{"op":"filter","table":"clinical"|"survival","field":F,"cmp":C,"value":V}
{"op":"any_of","table":"clinical","conditions":[{"field":F,"cmp":C,"value":V},...]}   OR across fields
{"op":"join","table":"normals","key":"patient_id","mode":"rows","add_to_cohort":false}   keep samples whose patient also has a normal-tissue sample; the normal samples are not added
{"op":"join","table":"normals","key":"patient_id","mode":"rows","add_to_cohort":true}   tumour-normal pairs: the patients' normal samples join the cohort as well
   either normals form takes "require_layers":[L,...] only when the request asks for data on the normal sample itself
{"op":"join","table":"slides","key":"sample_id","mode":"rows","slide_kinds":[K,...]}   K = slide kinds listed in DATA; [] = any kind
{"op":"where","condition":X}   any mix of AND / OR / NOT: X = {"all":[X,...]} | {"any":[X,...]} | {"not":X} | one test:
   {"layer":L} | {"sample_type":[...]} | {"gene":G,"mutated":true|false} | {"slide_kinds":[K,...]} ([] = any slide) | {"table":"clinical"|"survival","field":F,"cmp":C,"value":V}
   each sample is looked up by its own ID (no join needed); a value that isn't recorded leaves its test unknown, and unknown never passes, not even under "not"
   after a tumour-normal pairs join add "apply_to":"both" when every sample of a pair must meet it, "added_normals" when only the normal must
{"op":"one_per_patient","rule":"priority"}   the way to keep one sample per patient ("each patient once", "one per patient"): primary tumour, then metastatic, then normal (ties: lowest barcode); add "order":[...] only for a different sample-type order
{"op":"one_per_patient","rule":"earliest"|"latest"}   only when the request asks for the earliest / first-collected / latest / most recent sample (needs the clinical join; collection days may not reflect surgery dates)
C: "in"/"not_in" (list of exact values; missing values never pass) | "in_raw" (exact values marked *) | "stage_in" (whole groups "I","II","III","IV"; for sub-stages such as IIA use "in" with exact values) | "gte"/"lte" (number) | "between" ([low, high]) | "notnull" / "is_event" (value null)
Layer steps apply to the samples already in the cohort, never to normal samples added by a join: use require_layers for those.

OUTPUT: only this JSON object
{"interpretation":"one plain sentence",
 "steps":[...],
 "mappings":[{"phrase":"exact words from the request","meaning":"what you did"}],
 "corrections":[{"from":"...","to":"...","why":"..."}],
 "clarifications":[{"id":"q1","phrase":"words it is about","question":"...","why":"how the answer changes the cohort","options":[{"label":"...","steps":[...]}]}],
 "unsupported":[{"phrase":"...","reason":"...","alternative":"... or null"}],
 "assumptions":["low-impact assumptions only"]}

EXAMPLES (abbreviated)
"HR+ HER2- tumours" -> steps: sample_type [Primary Tumor]; join clinical; filter her2_status in [Negative]. clarifications: q1 phrase "HR+", "How should hormone-receptor positive be defined?", options: "ER+ or PR+ (usual definition)" [any_of er_status in [Positive], pr_status in [Positive]]; "ER+ only" [filter er_status in [Positive]]; "ER+ and PR+" [filter er_status in [Positive]; filter pr_status in [Positive]].
"tumours with RNA but no protein, followed for 5+ years" -> steps: sample_type; layer rna; exclude_layer protein; join survival; filter os_time gte 1826. corrections: "5+ years" -> "1826 days".
"smokers whose tumours carry a <gene> mutation" -> steps: sample_type [Primary Tumor]; mutation <gene> true. unsupported: "smokers", no smoking field in this release, alternative null.
"""


def build_system(catalog):
    return RULES + "\n" + catalog.static_context()


def build_user(request, catalog, current_spec=None, history=None):
    lines = [f"REQUEST: {request.strip()}"]
    if current_spec and current_spec.get("steps"):
        lines.append("This REQUEST changes the CURRENT COHORT below. Return the complete updated steps. Keep every step "
                     "the request doesn't mention exactly as it is (same field, values and order); add, remove or change "
                     "only what it asks. Use \"interpretation\" to say what changed, e.g. \"Removed the diagnostic slide "
                     "requirement and changed ER to Negative.\"")
        if current_spec.get("request"):
            lines.append(f"CURRENT COHORT was first asked as: {current_spec['request']}")
        if history:
            lines.append("EARLIER CHANGES IN THIS SESSION (oldest first): " + " | ".join(str(h) for h in history[-6:]))
        lines.append("CURRENT COHORT STEPS: " + json.dumps(current_spec["steps"], separators=(",", ":")))
    text = request + " " + (current_spec or {}).get("request", "")
    fields = catalog.relevant_fields(text)
    if fields:
        lines.append("VALUES OF COLUMNS THIS REQUEST MAY NEED (primary tumours):")
        for f in fields:
            lines.append(f"  {f} ({catalog.label('clinical', f)}): {catalog.describe_values('clinical', f)}")
    genes = catalog.gene_mentions(request)
    if genes:
        parts = []
        for g in genes:
            if g["found"]:
                parts.append(f"{g['gene']}: in the mutation file, mutated in {g['mutated_in']} primary tumours"
                             + (f" ({g['note']})" if g.get("note") else ""))
            else:
                close = f" (closest: {', '.join(g['closest'])})" if g["closest"] else ""
                parts.append(f"{g['token']}: NOT in the mutation file{close}")
        lines.append("GENES NAMED IN THE REQUEST: " + "; ".join(parts))
    missing = catalog.missing_concepts(request)
    if missing:
        lines.append("NOT IN THIS RELEASE (matched in the request): "
                     + "; ".join(f"'{word}' -> {what}" for word, what in missing))
    return "\n".join(lines)
