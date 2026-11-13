"""What this release actually contains, so the AI drafts from real fields and values, not from memory.

The catalogue profiles the committed manifests and the fetched raw files once: sample types, data layers,
slide kinds, every usable clinical column with its real values and counts, survival fields and the gene list.
It also finds the columns a request is likely about (lexicon + name + value matching), so only the relevant
values are sent to the model.
"""
import difflib
import re

import pandas as pd

from .. import ops
from ..data import CLINICAL_FIELDS, SURVIVAL_FIELDS

# Clinical columns beyond the nine friendly fields that a researcher commonly asks about.
EXTRA_CLINICAL = {
    "pathologic_T": "AJCC pathologic T category (tumour size/extent)",
    "pathologic_N": "AJCC pathologic N category (lymph nodes): N0 and its variants = node negative, N1/N2/N3 and their "
                    "variants = node positive, NX = not assessed",
    "pathologic_M": "AJCC pathologic M category (distant metastasis)",
    "number_of_lymphnodes_positive_by_he": "number of lymph nodes positive by H&E",
    "lymph_node_examined_count": "number of lymph nodes examined",
    "radiation_therapy": "received radiation therapy",
    "additional_radiation_therapy": "received additional radiation therapy",
    "additional_pharmaceutical_therapy": "received additional drug therapy (no drug names)",
    "targeted_molecular_therapy": "received targeted molecular therapy",
    "history_of_neoadjuvant_treatment": "treated before surgery (neoadjuvant)",
    "postoperative_rx_tx": "received postoperative treatment",
    "vital_status": "vital status in the clinical record (for outcome analyses prefer the survival table)",
    "person_neoplasm_cancer_status": "tumour status at last follow-up",
    "new_tumor_event_after_initial_treatment": "new tumour event (recurrence or progression) after initial treatment",
    "new_neoplasm_event_type": "type of new tumour event",
    "lost_follow_up": "lost to follow-up",
    "her2_immunohistochemistry_level_result": "HER2 IHC score",
    "lab_procedure_her2_neu_in_situ_hybrid_outcome_type": "HER2 in situ hybridisation (FISH) result",
    "er_level_cell_percentage_category": "percentage of ER-positive cells",
    "progesterone_receptor_level_cell_percent_category": "percentage of PR-positive cells",
    "margin_status": "surgical margin status",
    "breast_carcinoma_surgical_procedure_name": "type of surgery",
    "anatomic_neoplasm_subdivision": "tumour location in the breast (side, quadrant)",
    "metastatic_site_at_diagnosis": "site of metastasis at diagnosis",
    "other_dx": "prior or other malignancy",
    "year_of_initial_pathologic_diagnosis": "year of diagnosis",
    "days_to_last_followup": "days from diagnosis to last follow-up (clinical record)",
    "tissue_source_site": "tissue source site (submitting institution)",
    "PAM50_mRNA_nature2012": "PAM50 subtype from the 2012 TCGA paper (microarray); use only when a request names the 2012 paper",
    "ER_Status_nature2012": "ER status as curated for the 2012 TCGA paper; use only when a request names the paper",
    "PR_Status_nature2012": "PR status as curated for the 2012 TCGA paper; use only when a request names the paper",
    "HER2_Final_Status_nature2012": "HER2 status as curated for the 2012 TCGA paper; use only when a request names the paper",
}

# Words people use -> columns that can answer them.
CONCEPTS = [
    (("lymph node", "node positive", "node-positive", "node negative", "node-negative", "nodal", "axillary", "nodes"),
     ["pathologic_N", "number_of_lymphnodes_positive_by_he", "lymph_node_examined_count"]),
    (("tumor size", "tumour size", "t stage", "t category", "t1", "t2", "t3", "t4"), ["pathologic_T"]),
    (("distant metast", "m stage", "m1", "metastasis at diagnosis", "metastatic at diagnosis"),
     ["pathologic_M", "metastatic_site_at_diagnosis"]),
    (("radiation", "radiotherapy", "irradiat"), ["radiation_therapy", "additional_radiation_therapy"]),
    (("neoadjuvant", "before surgery", "pre-operative", "preoperative"), ["history_of_neoadjuvant_treatment"]),
    (("chemo", "drug", "pharmaceutical", "systemic", "endocrine", "hormone therap", "tamoxifen"),
     ["additional_pharmaceutical_therapy", "targeted_molecular_therapy"]),
    (("targeted", "trastuzumab", "herceptin"), ["targeted_molecular_therapy"]),
    (("postoperative", "post-operative", "adjuvant"), ["postoperative_rx_tx"]),
    (("recurren", "relapse", "came back", "new tumor", "new tumour"),
     ["new_tumor_event_after_initial_treatment", "new_neoplasm_event_type"]),
    (("alive", "living", "dead", "deceased", "vital"), ["vital_status"]),
    (("tumor free", "tumour free", "disease free", "cancer status"), ["person_neoplasm_cancer_status"]),
    (("lost to follow", "lost follow"), ["lost_follow_up"]),
    (("ihc score", "her2 score", "3+", "2+", "1+"), ["her2_immunohistochemistry_level_result"]),
    # categories defined by the IHC score and, for 2+, the ISH result
    (("her2-low", "her2 low", "her2low", "low her2", "low-her2", "her2-zero", "her2 zero", "her2-0", "her2 0"),
     ["her2_immunohistochemistry_level_result", "lab_procedure_her2_neu_in_situ_hybrid_outcome_type"]),
    (("fish", "in situ hybrid"), ["lab_procedure_her2_neu_in_situ_hybrid_outcome_type"]),
    (("margin",), ["margin_status"]),
    (("mastectomy", "lumpectomy", "surgery", "surgical"), ["breast_carcinoma_surgical_procedure_name"]),
    (("left breast", "right breast", "quadrant", "laterality"), ["anatomic_neoplasm_subdivision"]),
    (("prior cancer", "other cancer", "previous cancer", "prior malignan", "other malignan"), ["other_dx"]),
    (("year of diagnosis", "diagnosed in", "diagnosed after", "diagnosed before"), ["year_of_initial_pathologic_diagnosis"]),
    (("hospital", "institution", "submitting site", "source site", "centre", "center"), ["tissue_source_site"]),
    (("2012", "nature paper", "microarray", "original tcga"),
     ["PAM50_mRNA_nature2012", "ER_Status_nature2012", "PR_Status_nature2012", "HER2_Final_Status_nature2012"]),
    (("er percent", "percent er", "er level", "er %"), ["er_level_cell_percentage_category"]),
    (("pr percent", "percent pr", "pr level", "pr %"), ["progesterone_receptor_level_cell_percent_category"]),
]

# Things researchers ask for that this release does not contain.
NOT_IN_RELEASE = [
    (("smok", "tobacco", "cigarette"), "smoking or tobacco history"),
    (("alcohol", "drinking"), "alcohol use"),
    (("family history", "familial", "relatives"), "family history of cancer"),
    (("germline", "carrier", "hereditary", "inherited"),
     "germline (inherited) variant status (only somatic MC3 mutation calls exist)"),
    (("race", "racial", "ethnic", "african", "asian", "caucasian", "hispanic", "latina", "latino"), "race or ethnicity"),
    (("bmi", "body mass", "obes", "overweight"), "body mass index or patient weight"),
    (("grade", "nottingham", "differentiated"), "tumour grade"),
    (("ki-67", "ki67", "mib-1"), "Ki-67 proliferation index"),
    (("letrozole", "anastrozole", "paclitaxel", "doxorubicin", "regimen", "tamoxifen", "trastuzumab", "herceptin"),
     "named drugs or regimens (only yes/no therapy flags exist)"),
    (("complete response", "pcr", "treatment response", "responders"), "treatment response"),
    (("income", "insurance", "socioeconomic"), "socioeconomic data"),
]

PURE_MISSING = {"", "nan", "na", "n/a", "none", "null", "not available", "not reported", "unknown"}
# Words in almost every request: they say nothing about which column is meant.
GENERIC_WORDS = {"cancer", "cancers", "tumor", "tumors", "tumour", "tumours", "patient", "patients", "breast", "sample",
                 "samples", "data", "with", "have", "from", "that", "whose", "status", "primary", "cases", "women",
                 "people", "cohort", "only", "their", "which", "where", "than", "more", "less", "least"}
_STOP = {"of", "the", "a", "an", "and", "or", "to", "in", "on", "for", "by", "with", "status", "data", "value", "values"}


def _norm(text):
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")


def _tokens(text):
    return {t for t in _norm(text).split("_") if t and t not in _STOP}


def _has_term(text_lower, term):
    return re.search(r"(?<![a-z0-9])" + re.escape(term), text_lower) is not None


def value_flag(value):
    """'' for an ordinary value, '*' for a meaningful value the engine treats as missing (e.g. 'Equivocal')."""
    if ops.is_missing(value):
        text = str(value).strip()
        if text.startswith("[") or text.lower() in PURE_MISSING:
            return "missing"
        return "*"
    return ""


def profile_series(series):
    """Real values and counts of one column (counts are primary tumours)."""
    s = series.dropna().astype(str).str.strip()
    s = s[s != ""]
    flags = s.map(value_flag)
    recorded = s[flags != "missing"]
    out = {"n_recorded": int((flags == "").sum()), "n_missing": int(len(series) - len(recorded)),
           "values": [], "numeric": None}
    nums = pd.to_numeric(recorded, errors="coerce")
    if len(recorded) and nums.notna().mean() >= 0.9 and recorded.nunique() > 15:
        out["numeric"] = [float(nums.min()), float(nums.max())]
        return out
    counts = recorded.value_counts()
    out["values"] = [{"value": v, "count": int(n), "flag": value_flag(v)} for v, n in counts.items()]
    return out


class Catalog:
    _by_store = {}

    @classmethod
    def for_store(cls, store):
        key = id(store)
        if key not in cls._by_store:
            cls._by_store[key] = cls(store)
        return cls._by_store[key]

    def __init__(self, store):
        self.store = store
        m = store.manifest
        pt = m[m["sample_type"] == "Primary Tumor"]
        self.pt_ids = set(pt["sample_id"])
        self.sample_type_counts = {t: int(n) for t, n in m["sample_type"].value_counts().items()}
        self.one_primary_per_patient = bool(pt["patient_id"].is_unique)
        self.layer_counts = {layer: int(pt[col].sum()) for layer, col in ops.LAYER_COLUMNS.items()}
        slides = store.slides
        self.slide_counts = {k: int(n) for k, n in slides["slide_kind"].value_counts().items()}
        self.slide_sample_counts = {k: int(slides.loc[(slides["slide_kind"] == k) & slides["sample_id"].isin(self.pt_ids),
                                                      "sample_id"].nunique()) for k in self.slide_counts}
        self.friendly_by_column = {}
        self.profiles = {}           # (table, field) -> profile; field is the friendly name or the raw column
        self.descriptions = {}       # (table, field) -> plain description
        self.clinical_columns = []
        if store.available("clinical"):
            clin = store.table("clinical")
            clin_pt = clin[clin["sample_id"].isin(self.pt_ids)].drop_duplicates("sample_id")
            self.clinical_columns = [c for c in clin.columns if not _excluded(c)]
            for field, info in CLINICAL_FIELDS.items():
                col = store.resolve_field("clinical", field)
                if col:
                    self.friendly_by_column[("clinical", col)] = field
                    self.profiles[("clinical", field)] = profile_series(clin_pt[col])
                    self.descriptions[("clinical", field)] = info["label"]
            for col, desc in EXTRA_CLINICAL.items():
                if col in clin.columns and ("clinical", col) not in self.friendly_by_column:
                    self.profiles[("clinical", col)] = profile_series(clin_pt[col])
                    self.descriptions[("clinical", col)] = desc
            self._clin_pt = clin_pt
        if store.available("survival"):
            surv = store.table("survival")
            surv_pt = surv[surv["sample_id"].isin(self.pt_ids)].drop_duplicates("sample_id")
            for field, info in SURVIVAL_FIELDS.items():
                col = store.resolve_field("survival", field)
                if col:
                    self.friendly_by_column[("survival", col)] = field
                    self.profiles[("survival", field)] = profile_series(surv_pt[col])
                    self.descriptions[("survival", field)] = info["label"]
        self.genes = store.genes()
        self.gene_counts = {}
        if self.genes:
            mut = store.table("mutation")
            cols = [c for c in mut.columns if c in self.pt_ids]
            self.gene_counts = {g: int(n) for g, n in (mut[cols] > 0).sum(axis=1).sort_values(ascending=False).head(60).items()}
            self.mutation_samples = len(cols)
        self._gene_cache = {}

    # ---------- field lookup ----------
    def friendly(self, table, field):
        """Return the friendly field name for a raw column that has one, else the field unchanged."""
        col = self.store.resolve_field(table, field)
        return self.friendly_by_column.get((table, col), field)

    def profile(self, table, field):
        field = self.friendly(table, field)
        if (table, field) in self.profiles:
            return self.profiles[(table, field)]
        if table == "clinical" and hasattr(self, "_clin_pt"):
            col = self.store.resolve_field(table, field)
            if col and col in self._clin_pt.columns:
                self.profiles[(table, field)] = profile_series(self._clin_pt[col])
                return self.profiles[(table, field)]
        return None

    def label(self, table, field):
        return self.descriptions.get((table, self.friendly(table, field)), field)

    def all_fields(self, table):
        names = [f for (t, f) in self.profiles if t == table]
        if table == "clinical":
            names += [c for c in self.clinical_columns if ("clinical", c) not in self.friendly_by_column and c not in names]
        return names

    def best_fields(self, table, name, limit=4):
        """Rank fields of `table` by how well they match a (possibly invented) field name."""
        q_tokens, q_norm = _tokens(name), _norm(name)
        scored = []
        for f in self.all_fields(table):
            col = self.store.resolve_field(table, f) or f
            best = 0.0
            for cand in {f, col}:
                c_tokens = _tokens(cand)
                ratio = difflib.SequenceMatcher(None, q_norm, _norm(cand)).ratio()
                contain = len(q_tokens & c_tokens) / len(q_tokens) if q_tokens else 0.0
                score = max(ratio, 0.6 * contain + 0.4 * ratio)
                if q_tokens and q_tokens <= c_tokens:
                    score = max(score, 0.95)
                best = max(best, score)
            scored.append((round(best, 3), f))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return scored[:limit]

    # ---------- retrieval for one request ----------
    def relevant_fields(self, text, limit=6):
        """Columns (beyond the core fields shown to the model every time) that the request is probably about."""
        low = text.lower()
        hits = {}
        for terms, cols in CONCEPTS:
            if any(_has_term(low, t) for t in terms):
                for rank, col in enumerate(cols):
                    if ("clinical", col) in self.profiles:
                        hits[col] = max(hits.get(col, 0), 3 - 0.1 * rank)
        words = {w for w in re.findall(r"[a-z][a-z0-9+\-]{3,}", low)} - GENERIC_WORDS
        for (table, field), prof in self.profiles.items():
            if table != "clinical" or field in CLINICAL_FIELDS:
                continue
            name_tokens = _tokens(field) | _tokens(self.descriptions.get((table, field), ""))
            score = len(words & name_tokens)
            for v in prof["values"]:
                if any(w in v["value"].lower() for w in words if len(w) >= 5):
                    score += 1
                    break
            if score:
                hits[field] = max(hits.get(field, 0), score)
        return [f for f, _ in sorted(hits.items(), key=lambda kv: -kv[1])[:limit]]

    def missing_concepts(self, text):
        low = text.lower()
        return [(next(t for t in terms if _has_term(low, t)), what) for terms, what in NOT_IN_RELEASE
                if any(_has_term(low, t) for t in terms)]

    MUTANT_WORDS = {"MUTANT", "MUTATED", "MUTATION", "MUTATIONS", "MUT", "MT"}
    WILDTYPE_WORDS = {"WT", "WILDTYPE", "WILD"}

    def read_gene(self, token):
        """(gene symbol, mutated) for a written token, or (None, None). The symbol in any case, written with a
        separator ('tp-53', 'PIK3-CA'), or followed by a word that states the call: 'CDH1-mutant' -> True,
        'TP53_WT' / 'TP53-wild-type' -> False. Any other word ('MYC-amplified', 'PTEN-loss') isn't read as a gene."""
        up = str(token).upper().strip("-_/.")
        if up in self.genes:
            return up, None
        parts = [p for p in re.split(r"[-_/ ]", up) if p]
        if "".join(parts) in self.genes:
            return "".join(parts), None
        for i in range(1, len(parts)):
            head, rest = "".join(parts[:i]), "".join(parts[i:])
            if head in self.genes and rest in self.MUTANT_WORDS:
                return head, True
            if head in self.genes and rest in self.WILDTYPE_WORDS:
                return head, False
        return None, None

    def resolve_gene(self, token):
        return self.read_gene(token)[0]

    def gene_mentions(self, text):
        """Gene-like tokens in the request: found in the mutation file (with counts) or not (with close names)."""
        out = []
        seen, seen_genes = set(), set()
        for tok in re.findall(r"[A-Za-z][A-Za-z0-9\-_]{1,24}", text):
            tok = tok.strip("-_/")
            up = tok.upper()
            head = re.split(r"[-_/]", up)[0]
            if up in seen or up in NOT_GENES or head in NOT_GENES \
                    or re.fullmatch(r"(I{1,3}|IV)[ABC]?|[TNM][0-4X][A-D]?|PAM50", head):
                continue
            seen.add(up)
            # Only words written like a gene symbol: capitals ('PTEN', 'MYC-amplified') or with a digit ('tp53',
            # 'tp-53'). Lowercase words that happen to be symbols ('per' patient, 'set', 'rest') are ordinary English.
            if not (re.split(r"[-_/]", tok)[0].isupper() or re.search(r"\d", tok)):
                continue
            gene = self.resolve_gene(tok)
            note = ""
            if not gene and head in self.genes and len(re.split(r"[-_]", tok)) > 1:
                gene = head            # 'MYC-amplified': a gene in the file, but the word after it isn't about mutations
                note = (f"'{tok}' may describe something other than a mutation (e.g. copy number or expression), "
                        "which this release doesn't have per gene")
            if gene:
                if gene not in seen_genes:
                    seen_genes.add(gene)
                    out.append({"token": tok, "gene": gene, "found": True, "mutated_in": self.gene_mutated_count(gene),
                                "note": note})
                continue
            # As written: all capitals ('PTEN') or letters with digits ('tp35'); report the symbol part only.
            raw_head = re.split(r"[-_/]", tok)[0]
            if raw_head.isupper() or (re.search(r"\d", raw_head) and re.search(r"[A-Za-z]", raw_head)):
                name = head
            elif re.search(r"\d", tok) and re.search(r"[A-Za-z]", tok):
                name = up                                            # e.g. 'Ki-67': not a gene symbol as written
            else:
                continue
            if self.genes:
                out.append({"token": tok, "gene": name, "found": False, "closest": self.closest_genes(name)})
        return out

    def gene_mutated_count(self, gene):
        if gene in self.gene_counts:
            return self.gene_counts[gene]
        if gene not in self._gene_cache:
            mut = self.store.table("mutation")
            cols = [c for c in mut.columns if c in self.pt_ids]
            self._gene_cache[gene] = int((pd.to_numeric(mut.loc[gene, cols], errors="coerce") > 0).sum())
        return self._gene_cache[gene]

    def closest_genes(self, gene, n=3):
        """Likely intended genes for a typo: swapped characters first, then same length, then often-mutated genes."""
        pool = [g for g in self.genes if g[:1] == gene[:1]] or list(self.genes)
        scored = []
        for g in pool:
            ratio = difflib.SequenceMatcher(None, gene, g).ratio()
            if ratio < 0.6:
                continue
            bonus = (0.3 if sorted(g) == sorted(gene) else 0) + (0.1 if len(g) == len(gene) else 0) \
                + (0.05 if g in self.gene_counts else 0)
            scored.append((ratio + bonus, g))
        return [g for _, g in sorted(scored, key=lambda x: (-x[0], x[1]))[:n]]

    # ---------- text blocks for the prompt ----------
    def describe_values(self, table, field, max_values=25):
        prof = self.profile(table, field)
        if prof is None:
            return "(not in this release)"
        if prof["numeric"]:
            lo, hi = prof["numeric"]
            return f"number, {lo:g} to {hi:g}; recorded for {prof['n_recorded']} primary tumours"
        parts = [f"{v['value']}{'*' if v['flag'] == '*' else ''} {v['count']}" for v in prof["values"][:max_values]]
        if len(prof["values"]) > max_values:
            parts.append(f"... {len(prof['values']) - max_values} more")
        if prof["n_missing"]:
            parts.append(f"missing {prof['n_missing']}")
        return " | ".join(parts)

    def static_context(self):
        """Facts about the release that are the same for every request (kept first, so providers can cache it)."""
        lines = ["DATA: the event's frozen TCGA-BRCA release. Counts are primary tumours unless stated."]
        lines.append("SAMPLE TYPES (all samples): " + " | ".join(f"{t} {n}" for t, n in self.sample_type_counts.items())
                     + (". Every patient has exactly one primary tumour." if self.one_primary_per_patient else "")
                     + ". Every sample comes from a breast-cancer patient: Solid Tissue Normal is normal tissue taken "
                       "next to the tumour, and there are no samples from people without cancer.")
        lines.append("DATA LAYERS L: " + " | ".join(f"{k} {n}" for k, n in self.layer_counts.items()))
        lines.append("SLIDE KINDS (primary tumours with at least one): "
                     + " | ".join(f"{k} {n}" for k, n in self.slide_sample_counts.items()))
        if any(t == "clinical" for t, _ in self.profiles):
            lines.append("CLINICAL FIELDS (field: values with counts; * = the engine treats it as missing, select with in_raw):")
            for field in CLINICAL_FIELDS:
                if ("clinical", field) in self.profiles:
                    lines.append(f"  {field} ({self.descriptions[('clinical', field)]}): {self.describe_values('clinical', field)}")
            lines.append("OTHER CLINICAL COLUMNS (use the column name as the field; their values are sent when relevant):")
            for col, desc in EXTRA_CLINICAL.items():
                if ("clinical", col) in self.profiles:
                    lines.append(f"  {col}: {desc}")
        else:
            lines.append("CLINICAL FILE: not downloaded, so clinical filters cannot run.")
        if any(t == "survival" for t, _ in self.profiles):
            lines.append("SURVIVAL FIELDS (TCGA Clinical Data Resource; events are 1/0, times in days): os, os_time (days "
                         "to death or last contact = follow-up length), pfi, pfi_time, dss, dfi, redaction (value "
                         "'Redacted'). For an outcome EVENT in BRCA, PFI is the recommended endpoint.")
        if self.genes:
            top = " | ".join(f"{g} {n}" for g, n in list(self.gene_counts.items())[:15])
            lines.append(f"GENES: somatic MC3 gene-level calls for {len(self.genes):,} genes in {self.mutation_samples} "
                         f"primary tumours. Most often mutated: {top}. HER2 gene = ERBB2, ER gene = ESR1.")
        lines.append("NOT IN THIS RELEASE: " + "; ".join(what for _, what in NOT_IN_RELEASE) + ".")
        return "\n".join(lines)


NOT_GENES = {"ER", "PR", "HER2", "HER-2", "RNA", "DNA", "TCGA", "BRCA", "FFPE", "OS", "PFI", "PFS", "DSS", "DFI", "DFS",
             "CNV", "CNA", "RPPA", "IHC", "WSI", "DX", "TNBC", "MC3", "AJCC", "LUMA", "LUMB", "HR", "HR+", "AND", "OR",
             "NOT", "WITH", "THE", "FISH", "ISH", "ID", "IDS", "N0", "N1", "N2", "N3", "NX", "BMI", "IDC", "ILC", "PT",
             "PN", "PM", "RNA-SEQ", "ER-", "PR-", "HER2-", "LUM", "AI", "LLM", "CSV", "USA", "UK"}

_EXCLUDE = re.compile(r"^(_|sampleID$|sample_id$|patient_id$|bcr_|form_completion|pathology_report|informed_consent|"
                      r"project_code|disease_code|tumor_tissue_site|is_ffpe|vial_number|oct_embedded|sample_type|"
                      r"system_version|days_to_collection|days_to_initial)|(_text|_barcode)$|metastatic_breast_carcinom|"
                      r"^mtsttc_|^hr2_|^her2_neu_|^fluorescence_|^cytokeratin_|^her2_erbb|^her2_and_")


def _excluded(column):
    return bool(_EXCLUDE.search(column))
