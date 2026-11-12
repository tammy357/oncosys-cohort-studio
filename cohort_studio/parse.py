"""Turn a plain-English cohort request into an editable spec, without needing an AI or a network.

The parser is deliberately literal: it writes the joins the way a person would first try them
(one row per slide, for example), so the engine can show where a join inflates the count. It
also lists every word it did not use, so nothing in the request is silently ignored.
"""
import re

from . import ops
from .spec import new_spec

STOPWORDS = set("""
a an the with and or of for in on at to from by as be is are was were that which who whose whom this these those
all any both each every only also plus along together well have has having had their there where when
data available availability sample samples patient patients case cases cohort tcga brca breast cancer cancers
carcinoma carcinomas information info record records recorded known measured profiled profile profiles value values
type types level levels show me give find get list select build make want need i we us please our my can could would
like just one least some them it its per not no without set group people individuals subjects tissue tissues
""".split())

NOT_GENES = {"ER", "PR", "HER2", "RNA", "DNA", "TCGA", "BRCA", "PAM50", "FFPE", "OS", "PFI", "PFS", "DSS", "DFI",
             "DFS", "CNV", "CNA", "RPPA", "IHC", "WSI", "DX", "AND", "WITH", "THE", "TNBC", "MC3", "AJCC", "LUMA", "LUMB"}
ROMAN = {"1": "I", "2": "II", "3": "III", "4": "IV", "i": "I", "ii": "II", "iii": "III", "iv": "IV"}
ORDER = ["I", "II", "III", "IV"]


class _Text:
    """Lower-cased request text that remembers which characters a rule has used."""

    def __init__(self, text):
        self.original = text
        self.lower = text.lower()
        self.used = [False] * len(text)
        self.understood = []

    def find(self, pattern, meaning=None, flags=0, source=None):
        hay = self.original if source == "original" else self.lower
        hits = []
        for m in re.finditer(pattern, hay, flags):
            if any(self.used[m.start():m.end()]):
                continue
            for k in range(m.start(), m.end()):
                self.used[k] = True
            hits.append(m)
            if meaning:
                self.understood.append({"phrase": self.original[m.start():m.end()], "meaning": meaning})
        return hits

    def leftover_words(self):
        chars = [c if not u else " " for c, u in zip(self.original, self.used)]
        words = re.findall(r"[A-Za-z][A-Za-z0-9+\-]*", "".join(chars))
        return [w for w in words if w.lower().strip("-") not in STOPWORDS and len(w) > 1]


def _stage_values(a, b=None, joiner=None):
    a = ROMAN[a.lower()]
    if b is None:
        return [a]
    b = ROMAN[b.lower()]
    if joiner and joiner.strip() in ("-", "–", "to", "through"):
        i, j = sorted([ORDER.index(a), ORDER.index(b)])
        return ORDER[i:j + 1]
    return sorted({a, b}, key=ORDER.index)


def parse_request(text, store=None):
    t = _Text(text)
    genes_known = store.genes() if store is not None else set()
    notes = []
    clinical_filters, survival_filters, gene_steps = [], [], []

    def clin(field, cmp, value, meaning, pattern=None, hits=None):
        clinical_filters.append({"op": "filter", "table": "clinical", "field": field, "cmp": cmp, "value": value})

    # --- PAM50 subtypes (before "normal" and "HER2") ---
    pam = []
    # "luminal A or B": without this, "luminal A" matches and the lone "B" is silently dropped
    if t.find(r"\blum(?:inal)?[- ]?([ab])\s*(?:,?\s*(?:and/or|or|and)|,|/|&)\s*(?:lum(?:inal)?[- ]?)?(?!\1)[ab]\b",
              "PAM50 Luminal A or B"):
        pam += ["LumA", "LumB"]
    for pattern, value, words in [
        (r"normal[- ]like", "Normal", "PAM50 Normal-like"),
        (r"her2[- ]?(?:enriched|e\b)", "Her2", "PAM50 HER2-enriched"),
        (r"lum(?:inal)?[- ]?a\b|\bluma\b", "LumA", "PAM50 Luminal A"),
        (r"lum(?:inal)?[- ]?b\b|\blumb\b", "LumB", "PAM50 Luminal B"),
        (r"basal(?:[- ]like)?", "Basal", "PAM50 Basal-like"),
    ]:
        if t.find(pattern, words):
            pam.append(value)
    if t.find(r"\bluminal\b", "PAM50 Luminal A or B"):
        pam += ["LumA", "LumB"]
    if pam:
        clin("pam50", "in", sorted(set(pam)), "")
    elif t.find(r"pam50(?: subtypes?| calls?| labels?)?|molecular subtypes?|intrinsic subtypes?|rna subtypes?", "PAM50 subtype is recorded"):
        clin("pam50", "notnull", None, "")

    # --- label source: the 2012 TCGA Nature paper's calls instead of the clinical record ---
    nature = t.find(r"(?:(?:according to|based on|using|from|per)\s+)?(?:the\s+)?(?:20\s?12\s+)?(?:tcga\s+)?\bnature\b(?:\s+20\s?12)?"
                    r"(?:\s+(?:paper|study|publication))?(?:'?s)?(?:\s+(?:labels?|calls?|status))?",
                    "use the 2012 TCGA Nature paper's labels")

    # --- receptors ---
    if t.find(r"triple[- ]negative|\btnbc\b", "ER, PR and HER2 all negative (clinical)"):
        for f in ("er_status", "pr_status", "her2_status"):
            clin(f, "in", ["Negative"], "")

    def receptor_field(name):
        return ("er_status" if name in ("er", "estrogen receptor", "oestrogen receptor") else
                "pr_status" if name in ("pr", "progesterone receptor") else "her2_status")

    # One sign for a list: "negative for ER, PR and HER2", "ER and PR positive". Not "or" or "/":
    # "ER or PR positive" usually means either one, which separate filters can't express.
    rec = r"(?:er|pr|her2|her-2|estrogen receptor|oestrogen receptor|progesterone receptor)"
    rec_list = rf"\b{rec}(?:\s*(?:,\s*(?:and\s+)?|&|\band\b)\s*{rec}\b)+"
    for m in t.find(rf"\b(positive|negative) for ({rec_list}|\b{rec}\b)|({rec_list})\s*[- ]?\s*(positive|negative|\+|-(?![a-z]))"):
        sign, names = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
        value = "Positive" if sign in ("+", "positive") else "Negative"
        fields = list(dict.fromkeys(receptor_field(n) for n in re.findall(rec, names)))
        t.understood.append({"phrase": m.group(0), "meaning": " and ".join(f.split("_")[0].upper() for f in fields)
                             + f" {value.lower()} (clinical)"})
        for f in fields:
            clin(f, "in", [value], "")

    receptor_pattern = (r"\b(er|pr|her2|her-2|estrogen receptor|oestrogen receptor|progesterone receptor)"
                        r"\s*[- ]?\s*(?:status\s*)?(positive|negative|pos\b|neg\b|\+|-(?![a-z]))")
    for m in t.find(receptor_pattern):
        name, sign = m.group(1), m.group(2)
        field = receptor_field(name)
        value = "Positive" if sign in ("+", "positive", "pos") else "Negative"
        t.understood.append({"phrase": m.group(0), "meaning": f"{field.split('_')[0].upper()} {value.lower()} (clinical)"})
        clin(field, "in", [value], "")
    for m in t.find(r"\b(er|pr|her2|estrogen receptor|progesterone receptor) status\b"):
        field = ("er_status" if m.group(1) in ("er", "estrogen receptor") else
                 "pr_status" if m.group(1) in ("pr", "progesterone receptor") else "her2_status")
        if not any(f["field"] == field for f in clinical_filters):
            t.understood.append({"phrase": m.group(0), "meaning": f"{m.group(1).upper()} status is positive or negative"})
            clin(field, "in", ["Positive", "Negative"], "")

    # --- gene mutations (gene names are matched in the original text) ---
    gene_rx = r"[A-Za-z][A-Za-z0-9]{1,9}"
    for pattern, mutated in [
        (rf"\b(?:without|no|lacking)\s+(?:an?\s+)?({gene_rx})[- ]?(?:mutations?|mutated)\b", False),
        (rf"\b({gene_rx})[- ]?wild[- ]?type\b", False),
        (rf"\b({gene_rx})[- ]?(?:mutated|mutant|mutations?)\b", True),
        (rf"\bmutations?\s+(?:in|of)\s+({gene_rx})\b", True),
    ]:
        for m in re.finditer(pattern, t.original, re.IGNORECASE):
            gene = m.group(1).upper()
            ok = gene in genes_known if genes_known else (gene not in NOT_GENES and (any(ch.isdigit() for ch in gene) or m.group(1).isupper()))
            if gene in NOT_GENES or not ok or any(t.used[m.start():m.end()]):
                continue
            for k in range(m.start(), m.end()):
                t.used[k] = True
            t.understood.append({"phrase": m.group(0), "meaning": f"{gene} {'mutated' if mutated else 'not mutated'} (MC3 gene-level calls)"})
            gene_steps.append({"op": "mutation", "gene": gene, "mutated": mutated})

    # --- pairing with normal tissue ---
    pair = t.find(r"(?:matched|paired)[- ]normals?(?:[- ]tissue)?(?: samples?)?|tumou?r[- /]normal pairs?|"
                  r"(?:with|have|has|having|plus|and)\s+(?:a\s+|their\s+|its\s+)?(?:matched\s+|matching\s+)?normal[- ]tissue samples?|"
                  # "where the same patient also contributed adjacent normal tissue", "with an adjacent normal sample"
                  r"(?:(?:where|whose|in which)\s+)?(?:the\s+same\s+)?(?:patients?\s+)?(?:also\s+)?"
                  r"(?:with|have|has|had|having|contributed|provided|donated)\s+(?:a\s+|an\s+|their\s+)?"
                  r"(?:matched\s+|matching\s+|adjacent\s+)?normal(?:[- ]tissue)?(?: samples?)?(?:\s+from the same patients?)?|"
                  r"normal(?:[- ]tissue)? samples? from the same patients?",
                  "a normal-tissue sample from the same patient")
    add_normals = bool(pair) and bool(re.search(r"pairs?|and their normal|plus (?:their |the )?normal|include (?:the |their )?normal|"
                                                r"both (?:the )?tumou?r and (?:the )?normal", t.lower))

    # --- sample types ---
    types = []
    if t.find(r"all samples?|every sample|any sample type|all sample types", "all sample types"):
        types = list(ops.SAMPLE_TYPES)
    if t.find(r"metasta\w*", "metastatic samples"):
        types.append("Metastatic")
    if t.find(r"(?:solid[- ]tissue[- ])?normal(?:[- ]tissue)?(?: samples?)?|adjacent normal", "normal-tissue samples"):
        types.append("Solid Tissue Normal")
    if t.find(r"primary(?: tumou?rs?)?|tumou?rs?|(?:breast )?(?:cancers?|carcinomas?)", "primary tumor samples"):
        types.append("Primary Tumor")
    if not types and pair:
        types = ["Primary Tumor"]
    if not types:
        types = ["Primary Tumor"]
        notes.append("No sample type was named, so the cohort starts from primary tumors. Edit the first step if you meant something else.")
    if pair and "Primary Tumor" not in types and "Metastatic" not in types:
        types.append("Primary Tumor")
    types = [x for x in ops.SAMPLE_TYPES if x in types]

    # --- slides ---
    has_slide_word = bool(re.search(r"slides?|images?|histolog|patholog|h&e|\bwsi\b|whole[- ]slide|sections?", t.lower))
    kinds = []
    if t.find(r"diagnostic(?: \(ffpe\))?(?: slides?| images?)?|\bffpe\b(?: slides?)?|\bdx\b(?: slides?)?", "diagnostic (FFPE) slides") and \
            (has_slide_word or re.search(r"ffpe|\bdx\b", t.lower)):
        kinds += ["diagnostic (FFPE)"]
    if t.find(r"frozen(?:[- ]section)?(?: slides?| sections?| images?)?", "frozen-section slides"):
        kinds += ops.FROZEN_KINDS
    any_slide = t.find(r"(?:pathology |tissue |h&e |whole[- ]slide )?(?:slides?|images?|\bwsi\b)", "a pathology slide")
    want_slides = bool(kinds) or bool(any_slide)

    # --- data layers ---
    layers = []
    for pattern, layer, words in [
        (r"\brna\b(?:[- ]?seq)?|transcriptom\w*|(?<!protein )(?<!protein-)(?:gene )?expression", "rna", "RNA expression data"),
        (r"proteins?(?: expression| levels?| data)?|\brppa\b|proteom\w*", "protein", "protein (RPPA) data"),
        (r"copy[- ]number(?: data)?|\bcnv\b|\bcna\b|gistic", "copy_number", "copy-number data"),
        (r"mutation (?:data|calls?|profiles?)|somatic mutations?|\bmc3\b|\bmutations?\b|\bmutated\b", "mutation", "mutation data"),
        (r"clinical (?:data|records?|information)", "clinical", "a clinical record"),
    ]:
        if t.find(pattern, words):
            layers.append(layer)
    if gene_steps and "mutation" not in layers:
        layers.append("mutation")

    # --- survival endpoints ---
    for pattern, field, cmp, words in [
        (r"progression[- ]free(?: interval| survival)?|\bpfi\b|\bpfs\b", "pfi", "notnull", "progression-free interval is recorded"),
        (r"overall survival|\bos\b", "os", "notnull", "overall survival is recorded"),
        (r"disease[- ]specific(?: survival)?|\bdss\b", "dss", "notnull", "disease-specific survival is recorded"),
        (r"disease[- ]free(?: interval| survival)?|\bdfi\b|\bdfs\b", "dfi", "notnull", "disease-free interval is recorded"),
        (r"\b(?:who )?died\b|\bdeceased\b|\bdeaths?\b", "os", "is_event", "died during follow-up (overall survival event)"),
        (r"\bprogressed\b|with (?:a )?progression", "pfi", "is_event", "had a progression event"),
    ]:
        if t.find(pattern, words):
            survival_filters.append({"op": "filter", "table": "survival", "field": field, "cmp": cmp, "value": None})
    if not survival_filters and t.find(r"survival|follow[- ]?up|outcomes?", "curated survival data"):
        layers.append("survival")

    # --- other clinical fields ---
    stages = []
    for m in t.find(r"stages?\s*(iv|iii|ii|i|[1-4])(?![a-z0-9])(?:\s*(-|–|to|through|or|and|,)\s*(?:stage\s*)?(iv|iii|ii|i|[1-4])(?![a-z0-9]))?"):
        stages += _stage_values(m.group(1), m.group(3), m.group(2))
        t.understood.append({"phrase": m.group(0), "meaning": "stage " + " or ".join(stages)})
    if t.find(r"early[- ]stage", "stage I or II (assumed meaning of 'early stage')"):
        stages += ["I", "II"]
        notes.append("'Early stage' was read as stage I or II. Edit the step if you meant something else.")
    if t.find(r"late[- ]stage|advanced(?:[- ]stage)?", "stage III or IV (assumed meaning of 'advanced')"):
        stages += ["III", "IV"]
        notes.append("'Advanced' or 'late stage' was read as stage III or IV. Edit the step if you meant something else.")
    if stages:
        clin("stage", "stage_in", sorted(set(stages), key=ORDER.index), "")

    for m in t.find(r"(?:aged?\s*)?(?:between|from)\s*(\d{1,3})\s*(?:-|–|to|and)\s*(\d{1,3})(?:\s*years?(?: old)?)?|aged?\s*(\d{1,3})\s*(?:-|–|to)\s*(\d{1,3})"):
        lo, hi = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        t.understood.append({"phrase": m.group(0), "meaning": f"age at diagnosis {lo}-{hi}"})
        clin("age", "between", [int(lo), int(hi)], "")
    age_word = r"(?:aged?|age at (?:initial )?diagnosis(?: of)?)"
    who = r"(?:patients?|women|woman|men|man|people|cases|individuals)"
    age_rx = (rf"(?:{age_word}\s*|{who}\s+(?:who are\s+|aged\s+)?)"
              r"(over|above|older than|at least|>=|>|under|below|younger than|less than|at most|<=|<)\s*(\d{1,3})(?!\s*%)(?:\s*years?(?: old)?)?"
              r"|(over|above|older than|at least|>=|>|under|below|younger than|less than|at most|<=|<)\s*(\d{1,3})\s*(?:years?|yrs?)(?: old)?")
    for m in t.find(age_rx):
        word, n = (m.group(1), int(m.group(2))) if m.group(1) else (m.group(3), int(m.group(4)))
        if n > 120:
            continue
        if word in ("over", "above", "older than", ">"):
            cmp, v = "gte", n + 1
        elif word in ("at least", ">="):
            cmp, v = "gte", n
        elif word in ("under", "below", "younger than", "less than", "<"):
            cmp, v = "lte", n - 1
        else:
            cmp, v = "lte", n
        t.understood.append({"phrase": m.group(0), "meaning": f"age at diagnosis {'≥' if cmp == 'gte' else '≤'} {v}"})
        clin("age", cmp, v, "")
    # "50 or older", "40 and under", "aged 50+": the comparison comes after the number (both ends included).
    # Without an age word in front, only "years" or older/younger make it an age ("2 or more slides" isn't).
    words = r"older|over|above|up|more|younger|under|below|less"
    years = r"(?:\s*years?(?: old)?)?"
    age_after_rx = (rf"(?:{age_word}\s*|{who}\s+(?:aged\s+)?)(?P<n1>\d{{1,3}}){years}\s*(?:or|and)\s*(?P<w1>{words})\b{years}"
                    rf"|(?<![\w.])(?P<n2>\d{{1,3}})(?:\s*years?(?: old)?\s*(?:or|and)\s*(?P<w2>{words})"
                    rf"|\s*(?:or|and)\s*(?P<w3>older|younger))\b{years}"
                    rf"|(?:{age_word}|{who})\s*(?P<n3>\d{{1,3}})\s*\+")
    for m in t.find(age_after_rx):
        n = int(m["n1"] or m["n2"] or m["n3"])
        if n > 120:
            continue
        cmp = "lte" if (m["w1"] or m["w2"] or m["w3"]) in ("younger", "under", "below", "less") else "gte"
        t.understood.append({"phrase": m.group(0), "meaning": f"age at diagnosis {'≥' if cmp == 'gte' else '≤'} {n}"})
        clin("age", cmp, n, "")

    if t.find(r"(?:invasive |infiltrating )?ductal", "infiltrating ductal carcinoma"):
        clin("histology", "in", ["Infiltrating Ductal Carcinoma"], "")
    if t.find(r"(?:invasive |infiltrating )?lobular", "infiltrating lobular carcinoma"):
        clin("histology", "in", ["Infiltrating Lobular Carcinoma"], "")
    if t.find(r"\b(?:female|women|woman)\b", "recorded gender female"):
        clin("gender", "in", ["FEMALE"], "")
    if t.find(r"\b(?:male|men|man)\b", "recorded gender male"):
        clin("gender", "in", ["MALE"], "")
    # "post (" / "pre (", not "post" / "pre": the labels read "Post (...)" and "Pre (...)", and a bare word
    # would also match "Indeterminate (neither Pre or Postmenopausal)".
    if t.find(r"post[- ]?menopausal", "post-menopausal"):
        clin("menopause", "contains", ["post ("], "")
    if t.find(r"pre[- ]?menopausal", "pre-menopausal"):
        clin("menopause", "contains", ["pre ("], "")

    # --- one sample per patient ---
    choose = None
    per_patient = r"(?:\s+(?:per|for each|of each|from each|for every)\s+patient)?"
    if t.find(r"(?:the\s+)?(?:earliest|first|initial)(?:[- ]collected)?\s+(?:samples?|specimens?)" + per_patient,
              "keep each patient's earliest collected sample"):
        choose = {"op": "one_per_patient", "rule": "earliest"}
    elif t.find(r"(?:the\s+)?(?:latest|last|most recent|newest)(?:[- ]collected)?\s+(?:samples?|specimens?)" + per_patient,
                "keep each patient's latest collected sample"):
        choose = {"op": "one_per_patient", "rule": "latest"}
    elif t.find(r"(?:only\s+)?(?:one|a single|1)\s+(?:samples?|specimens?)\s+(?:per|for each|from each|for every)\s+patient|"
                r"one per patient|(?:count|counting) each patient once|unique patients?|dedup\w* (?:by|on) patients?",
                "keep one sample per patient (primary tumor first, then metastatic, then normal)"):
        choose = {"op": "one_per_patient", "rule": "priority"}

    key = "sample_id"
    if t.find(r"(?:(?:joined|join|matched|linked)\s+)?(?:by|on|using)(?: the)? patient(?: id| barcode)?s?|patient[- ]level", "join on the patient barcode"):
        key = "patient_id"
        notes.append("Joins use the patient barcode, as requested. Patients with several samples may be counted twice; the ledger will show it.")

    if nature:
        # same filters, read from the paper's columns (its PAM50 values are spelled out)
        columns = {"er_status": "ER_Status_nature2012", "pr_status": "PR_Status_nature2012",
                   "her2_status": "HER2_Final_Status_nature2012", "pam50": "PAM50_mRNA_nature2012"}
        pam50_names = {"LumA": "Luminal A", "LumB": "Luminal B", "Her2": "HER2-enriched", "Basal": "Basal-like",
                       "Normal": "Normal-like"}
        switched = [f for f in clinical_filters if f["field"] in columns]
        for f in switched:
            if f["field"] == "pam50" and isinstance(f["value"], list):
                f["value"] = [pam50_names.get(v, v) for v in f["value"]]
            f["field"] = columns[f["field"]]
        if not switched:
            notes.append("The 2012 Nature paper's labels cover ER, PR, HER2 and PAM50; the request names none of them.")

    # --- assemble steps in a readable order ---
    steps = [{"op": "sample_type", "values": types}]
    steps += [{"op": "layer", "layer": layer} for layer in dict.fromkeys(layers)]
    steps += gene_steps
    if clinical_filters:
        steps.append({"op": "join", "table": "clinical", "key": key, "mode": "rows"})
        steps += clinical_filters
    if survival_filters:
        steps.append({"op": "join", "table": "survival", "key": key, "mode": "rows"})
        steps += survival_filters
    if pair:
        steps.append({"op": "join", "table": "normals", "key": "patient_id", "mode": "rows", "add_to_cohort": add_normals})
    if want_slides:
        steps.append({"op": "join", "table": "slides", "key": "sample_id", "mode": "rows",
                      "slide_kinds": list(dict.fromkeys(kinds))})
    if choose:
        if choose["rule"] != "priority" and not clinical_filters:
            steps.append({"op": "join", "table": "clinical", "key": key, "mode": "rows"})
        steps.append(choose)

    spec = new_spec(text, steps, parser="rules")
    spec["notes"] = notes
    spec["understood"] = t.understood
    spec["unparsed"] = t.leftover_words()
    return spec
