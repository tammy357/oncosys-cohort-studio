"""Autocomplete: oncology terms this release can answer, with real counts, plus the user's own past searches."""
import re

from cohort_studio import ops
from cohort_studio.assistant import run_cohort


def _count(profile, *values):
    if not profile:
        return None
    want = {v.lower() for v in values}
    return sum(v["count"] for v in profile["values"] if v["value"].lower() in want) or None


def _starts(profile, *prefixes):
    if not profile:
        return None
    return sum(v["count"] for v in profile["values"] if v["value"].lower().startswith(prefixes)) or None


class Suggester:
    def __init__(self, store, catalog):
        self.terms = []
        cat = catalog
        add = self._add
        p = lambda f: cat.profile("clinical", f)  # noqa: E731
        tumours = len(cat.pt_ids)

        # samples and data layers
        add("primary tumors", "samples", tumours)
        add("tumor-normal pairs", "samples", cat.sample_type_counts.get("Solid Tissue Normal"))
        add("matched normal tissue", "samples", cat.sample_type_counts.get("Solid Tissue Normal"))
        add("metastatic samples", "samples", cat.sample_type_counts.get("Metastatic"))
        add("one sample per patient", "samples", None)
        add("all sample types", "samples", sum(cat.sample_type_counts.values()))
        for words, layer in [("RNA expression data", "rna"), ("RNA-seq", "rna"), ("protein (RPPA) data", "protein"),
                             ("copy-number data", "copy_number"), ("mutation data", "mutation"),
                             ("survival data", "survival"), ("clinical data", "clinical")]:
            add(words, "data layer", cat.layer_counts.get(layer))
        add("no protein data", "data layer", tumours - cat.layer_counts.get("protein", 0))
        add("all five data layers", "data layer", None)
        # slides
        add("diagnostic slide", "slides", cat.slide_sample_counts.get("diagnostic (FFPE)"))
        add("frozen-section slide", "slides", cat.slide_sample_counts.get("frozen, top section"))
        add("any pathology slide", "slides", None)
        # receptors and subtypes
        for name, field in [("ER", "er_status"), ("PR", "pr_status"), ("HER2", "her2_status")]:
            add(f"{name}-positive", "receptor (IHC)", _count(p(field), "Positive"))
            add(f"{name}-negative", "receptor (IHC)", _count(p(field), "Negative"))
        add("HER2-equivocal", "receptor (IHC)", _count(p("her2_status"), "Equivocal"))
        add("triple-negative", "receptor (IHC)", None)
        add("hormone receptor positive", "receptor (IHC)", None)
        add("HR-positive, HER2-negative", "receptor (IHC)", None)
        for words, value in [("Luminal A", "LumA"), ("Luminal B", "LumB"), ("HER2-enriched", "Her2"),
                             ("Basal-like", "Basal"), ("Normal-like", "Normal")]:
            add(words, "PAM50 subtype", _count(p("pam50"), value))
        add("with a PAM50 subtype", "PAM50 subtype", None)
        add("PAM50 from the 2012 TCGA paper", "PAM50 subtype", None)
        # stage
        stage = p("stage")
        if stage:
            groups = {}
            for v in stage["values"]:
                g = ops.stage_group(v["value"])
                if g:
                    groups[g] = groups.get(g, 0) + v["count"]
                add(v["value"].replace("Stage", "stage"), "stage", v["count"])
            for g, n in groups.items():
                add(f"stage {g} (all sub-stages)", "stage", n)
        add("early stage (I-II)", "stage", None)
        add("advanced stage (III-IV)", "stage", None)
        # nodes, treatment, outcome
        n = p("pathologic_N")
        add("lymph node positive", "lymph nodes", _starts(n, "n1", "n2", "n3"))
        add("lymph node negative", "lymph nodes", _starts(n, "n0"))
        add("received radiation therapy", "treatment", _count(p("radiation_therapy"), "YES"))
        add("no radiation therapy", "treatment", _count(p("radiation_therapy"), "NO"))
        add("neoadjuvant treatment", "treatment", _count(p("history_of_neoadjuvant_treatment"), "Yes"))
        add("no neoadjuvant treatment", "treatment", _count(p("history_of_neoadjuvant_treatment"), "No"))
        add("targeted molecular therapy", "treatment", _count(p("targeted_molecular_therapy"), "YES"))
        add("recurrence after treatment", "outcome", _count(p("new_tumor_event_after_initial_treatment"), "YES"))
        for words, field in [("overall survival", "os"), ("progression-free interval", "pfi"),
                             ("disease-specific survival", "dss"), ("disease-free interval", "dfi")]:
            add(words, "outcome", cat.profile("survival", field) and cat.profile("survival", field)["n_recorded"])
        add("died during follow-up", "outcome", _count(cat.profile("survival", "os"), "1"))
        add("progressed", "outcome", _count(cat.profile("survival", "pfi"), "1"))
        add("at least 5 years of follow-up", "outcome", None)
        # people and histology
        menopause = p("menopause")
        add("postmenopausal", "patients", _starts(menopause, "post"))
        add("premenopausal", "patients", _starts(menopause, "pre"))
        add("perimenopausal", "patients", _starts(menopause, "peri"))
        add("age under 40", "patients", None)
        add("age 65 and over", "patients", None)
        add("male patients", "patients", _count(p("gender"), "MALE"))
        hist = p("histology")
        for v in (hist or {}).get("values", []):
            if not v["value"].lower().startswith(("other", "mixed")):
                add(v["value"].lower(), "histology", v["count"])
        # genes
        for g, count in list(cat.gene_counts.items())[:40]:
            add(f"{g} mutated", "gene", count)
            add(f"{g} wild-type", "gene", None)
        # composite counts (a few engine runs, once)
        for words, steps in [
            ("triple-negative", [{"op": "sample_type", "values": ["Primary Tumor"]},
                                 {"op": "join", "table": "clinical", "key": "sample_id", "mode": "rows"}]
             + [{"op": "filter", "table": "clinical", "field": f, "cmp": "in", "value": ["Negative"]}
                for f in ("er_status", "pr_status", "her2_status")]),
        ]:
            if store.available("clinical"):
                ids, _ = run_cohort(store, words, steps)
                for t in self.terms:
                    if t["text"] == words:
                        t["count"] = len(ids) if ids is not None else None

    def _add(self, text, category, count):
        self.terms.append({"text": text, "category": category, "count": count, "low": text.lower(),
                           "words": re.split(r"[\s\-/(),]+", text.lower())})

    def suggest(self, text, recent=(), limit=8):
        raw = text or ""
        spans = [(m.start(), m.group(0).lower()) for m in re.finditer(r"\S+", raw)]
        if not spans:
            return [{"text": q, "category": "recent search", "count": None, "start": 0} for q in list(recent)[:limit]]
        out, seen = [], set()
        whole = raw.strip().lower()
        for q in recent:
            if len(whole) >= 3 and whole in q.lower() and q.lower() != whole:
                out.append((30, {"text": q, "category": "recent search", "count": None, "start": 0}))
        if not raw.endswith(" "):
            for n in (3, 2, 1):
                if len(spans) < n:
                    continue
                start = spans[-n][0]
                frag = raw[start:].lower().strip(",.;:")
                if len(frag) < 2:
                    continue
                for t in self.terms:
                    if t["low"] == frag:
                        continue
                    if t["low"].startswith(frag):
                        score = 10 + 2 * n
                    elif n == 1 and any(w.startswith(frag) for w in t["words"]):
                        score = 6
                    elif n == 1 and len(frag) >= 3 and frag in t["low"]:
                        score = 3
                    else:
                        continue
                    out.append((score, {"text": t["text"], "category": t["category"], "count": t["count"], "start": start}))
        out.sort(key=lambda x: (-x[0], -(x[1]["count"] or 0)))
        result = []
        for _, s in out:
            if s["text"].lower() not in seen:
                seen.add(s["text"].lower())
                result.append(s)
            if len(result) >= limit:
                break
        return result
