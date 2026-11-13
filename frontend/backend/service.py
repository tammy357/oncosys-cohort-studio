"""What the web app does: draft (AI, built-in parser or advanced form), answer questions, run, fix, export, remember."""
import copy
import json
import pathlib
import threading

import pandas as pd

from cohort_studio import engine, exports, verdict
from cohort_studio.assistant import (AIError, Catalog, LLMClient, draft_from_spec, make_draft, materialize, review_items,
                                     to_spec)
from cohort_studio.assistant.edits import diff_steps, ensure_joins, same_steps
from cohort_studio.assistant.verify import Checker, canonicalize
from cohort_studio.spec import validate
from cohort_studio.data import DataStore
from cohort_studio.parse import parse_request
from cohort_studio.spec import describe_step

from cohort_studio.assistant import insights as ai_insights
from cohort_studio.assistant.draft import run_cohort

from .advanced import FormError, build_spec, form_options
from .compare import CompareError, compare, metrics_for_model
from .limits import Caller, LimitExceeded, UsageGuard
from .suggest import Suggester

MAX_QUERY_CHARS = 2000

_PT = {"op": "sample_type", "values": ["Primary Tumor"]}
_JC = {"op": "join", "table": "clinical", "key": "sample_id", "mode": "rows"}


def _f(field, cmp, value, table="clinical"):
    return {"op": "filter", "table": table, "field": field, "cmp": cmp, "value": value}


# Ready-made cohorts. Clicking one sends the query through the assistant like any search; `steps` is a
# hand-written equivalent used only to draw the card's attrition curve from the real data.
PRESETS = [
    {"label": "The double-count demo", "tag": "Join check", "query": "primary tumors with both RNA and protein data and a diagnostic slide",
     "description": "The brief's live demo. A slide join inflates the rows; you fix it in one click.",
     "steps": [_PT, {"op": "layer", "layer": "rna"}, {"op": "layer", "layer": "protein"},
               {"op": "join", "table": "slides", "key": "sample_id", "mode": "exists", "slide_kinds": ["diagnostic (FFPE)"]}]},
    {"label": "HR+ / HER2− with protein", "tag": "Asks first", "query": "hormone receptor positive, HER2 negative tumors with protein data",
     "description": "HR+ has three common definitions. You choose, seeing the count each one gives.",
     "steps": [_PT, {"op": "layer", "layer": "protein"}, _JC,
               {"op": "any_of", "table": "clinical", "conditions": [{"field": "er_status", "cmp": "in", "value": ["Positive"]},
                                                                    {"field": "pr_status", "cmp": "in", "value": ["Positive"]}]},
               _f("her2_status", "in", ["Negative"])]},
    {"label": "Tumour–normal pairs", "tag": "Pairs", "query": "tumor and matching normal samples from the same patients, both with RNA",
     "description": "Both halves of every pair must have RNA. The normals join the cohort.",
     "steps": [_PT, {"op": "layer", "layer": "rna"},
               {"op": "join", "table": "normals", "key": "patient_id", "mode": "rows", "add_to_cohort": True, "require_layers": ["rna"]}]},
    {"label": "Basal-like, TP53 wild-type", "tag": "Negation", "query": "basal-like tumors without a TP53 mutation",
     "description": "'Without' is never dropped. You get the tumours with no TP53 call.",
     "steps": [_PT, {"op": "mutation", "gene": "TP53", "mutated": False}, _JC, _f("pam50", "in", ["Basal"])]},
    {"label": "Five years of follow-up", "tag": "Outcomes", "query": "patients with at least 5 years of follow-up and RNA data",
     "description": "Years become days, and it checks which survival clock you mean.",
     "steps": [_PT, {"op": "layer", "layer": "rna"}, {"op": "join", "table": "survival", "key": "sample_id", "mode": "rows"},
               _f("os_time", "gte", 1826, "survival")]},
    {"label": "Node-positive luminal A", "tag": "Clinical", "query": "lymph node positive luminal A tumors with a diagnostic slide",
     "description": "Grounded in the release's real pathologic N categories, not a guess.",
     "steps": "node_positive"},
    {"label": "One sample per patient", "tag": "Per patient", "query": "one sample per patient with mutation and copy-number data",
     "description": "Keeps the primary tumour first, so no patient is counted twice.",
     "steps": [{"op": "sample_type", "values": ["Primary Tumor", "Solid Tissue Normal", "Metastatic"]},
               {"op": "layer", "layer": "mutation"}, {"op": "layer", "layer": "copy_number"},
               {"op": "one_per_patient", "rule": "priority"}]},
    {"label": "Smokers: an honest no", "tag": "Honest gaps", "query": "HER2-positive tumors from smokers",
     "description": "There is no smoking data in this release. The tool says so instead of guessing.",
     "steps": None},
]
FUNNEL_CHARACTERISTICS = ["PAM50 subtype", "ER status (IHC)", "Stage", "Age band", "Sample type"]
FIELD_GROUP_ORDER = ["Receptors and subtype", "Tumour", "Patient", "Treatment", "Outcome · clinical record",
                     "Outcome · curated survival", "Other clinical fields"]
FIELD_GROUPS = {f: "Receptors and subtype" for f in (
    "er_status", "pr_status", "her2_status", "pam50", "PAM50_mRNA_nature2012", "ER_Status_nature2012", "PR_Status_nature2012",
    "HER2_Final_Status_nature2012", "her2_immunohistochemistry_level_result", "lab_procedure_her2_neu_in_situ_hybrid_outcome_type",
    "er_level_cell_percentage_category", "progesterone_receptor_level_cell_percent_category")}
FIELD_GROUPS.update({f: "Tumour" for f in (
    "stage", "histology", "pathologic_T", "pathologic_N", "pathologic_M", "number_of_lymphnodes_positive_by_he",
    "lymph_node_examined_count", "margin_status", "anatomic_neoplasm_subdivision", "metastatic_site_at_diagnosis")})
FIELD_GROUPS.update({f: "Patient" for f in ("age", "gender", "menopause", "other_dx", "year_of_initial_pathologic_diagnosis",
                                            "tissue_source_site")})
FIELD_GROUPS.update({f: "Treatment" for f in (
    "radiation_therapy", "additional_radiation_therapy", "additional_pharmaceutical_therapy", "targeted_molecular_therapy",
    "history_of_neoadjuvant_treatment", "postoperative_rx_tx", "breast_carcinoma_surgical_procedure_name")})
FIELD_GROUPS.update({f: "Outcome · clinical record" for f in (
    "vital_status", "person_neoplasm_cancer_status", "new_tumor_event_after_initial_treatment", "new_neoplasm_event_type",
    "lost_follow_up", "days_to_last_followup")})
GROUPS = [("LumA", "Luminal A"), ("LumB", "Luminal B"), ("Basal", "Basal-like"), ("Her2", "HER2-enriched"),
          ("Normal", "Normal-like"), ("Unassigned", "No PAM50 call"), ("Normal tissue", "Normal tissue"),
          ("Metastatic", "Metastatic")]
FILES = {"cohort_samples.csv": "text/csv", "reproduce_cohort.py": "text/x-python", "cohort_spec.json": "application/json",
         "findings.json": "application/json", "verdict.json": "application/json", "ledger.csv": "text/csv",
         "exclusions.csv": "text/csv", "sample_selection.csv": "text/csv", "coverage.csv": "text/csv"}


def compact_mutation(data_dir):
    """The mutation calls as ops.load_mutation returns them, but read in chunks and stored as 1-byte integers.

    The file is 0/1 calls for ~40,000 genes x ~800 samples: as int64 that is ~260 MB, with a ~600 MB spike while
    reading, which is more than a small server (for example a 512 MB instance) has. As int8 it is ~32 MB.
    Any chunk that isn't small whole numbers is kept as float32, so no value changes.
    """
    path = pathlib.Path(data_dir) / "raw" / "mutation_gene_level.tsv.gz"
    parts = []
    for chunk in pd.read_csv(path, sep="\t", index_col=0, chunksize=4000):
        whole = all(pd.api.types.is_integer_dtype(t) for t in chunk.dtypes)
        small = whole and (chunk.abs().to_numpy().max(initial=0) < 128)
        parts.append(chunk.astype("int8" if small else "float32"))
    m = pd.concat(parts)
    m.columns = [str(c)[:15] for c in m.columns]
    return m


class ServiceError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class Service:
    def __init__(self, settings, storage, client=None):
        self.settings, self.db = settings, storage
        self.store = DataStore(settings.data_dir)
        if self.store.available("mutation") and "mutation" not in getattr(self.store, "_tables", {"mutation": 1}):
            self.store._tables["mutation"] = compact_mutation(self.store.data_dir)
        for table in ("clinical", "survival", "mutation"):
            if self.store.available(table):
                self.store.table(table)
        self.catalog = Catalog.for_store(self.store)
        self.client = client
        if self.client is None and settings.ai_enabled:
            self.client = LLMClient(cache=storage.llm_cache)
        self.suggester = Suggester(self.store, self.catalog)
        self.guard = UsageGuard(settings, storage)
        self._lock = threading.Lock()
        self.ai_calls, self.tokens = 0, {"input": 0, "output": 0, "cached_input": 0, "reasoning": 0}
        self.presets = [self._preset_view(p) for p in PRESETS]
        self.specimen = self._specimen()
        self.fields = self.field_catalog()

    # ---------- what the page draws ----------
    def _preset_view(self, preset):
        """A preset card plus its real attrition curve (sample counts after each step)."""
        out = {k: v for k, v in preset.items() if k != "steps"}
        steps = preset["steps"]
        if steps == "node_positive":
            prof = self.catalog.profile("clinical", "pathologic_N")
            vals = [v["value"] for v in (prof or {}).get("values", []) if v["value"].lower().startswith(("n1", "n2", "n3"))]
            steps = [_PT, _JC, _f("pam50", "in", ["LumA"]), _f("pathologic_N", "in", vals),
                     {"op": "join", "table": "slides", "key": "sample_id", "mode": "exists", "slide_kinds": ["diagnostic (FFPE)"]}]
        out["curve"], out["final"] = None, None
        if steps:
            try:
                r = engine.run({"request": preset["query"], "steps": canonicalize(copy.deepcopy(steps))[0]}, self.store)
                if r.ok:
                    out["curve"] = [len(r.start_samples)] + [s.samples_out for s in r.steps]
                    out["final"] = len(r.cohort_ids)
            except Exception:  # a preset whose data isn't downloaded just has no curve
                pass
        return out

    def _specimen(self):
        """One entry per sample for the background field: [sample_id, group index]."""
        m = self.store.manifest
        pam = {}
        col = self.store.resolve_field("clinical", "pam50") if self.store.available("clinical") else None
        if col:
            clin = self.store.table("clinical").drop_duplicates("sample_id").set_index("sample_id")
            pam = clin[col].to_dict()
        names = [g for g, _ in GROUPS]
        samples = []
        for sid, st in zip(m["sample_id"], m["sample_type"]):
            if st == "Solid Tissue Normal":
                g = "Normal tissue"
            elif st == "Metastatic":
                g = "Metastatic"
            else:
                g = pam.get(sid) if pam.get(sid) in names[:5] else "Unassigned"
            samples.append([sid, names.index(g)])
        counts = [sum(1 for _, gi in samples if gi == i) for i in range(len(GROUPS))]
        return {"groups": [{"name": g, "label": lab, "count": n} for (g, lab), n in zip(GROUPS, counts)],
                "samples": samples}

    # ---------- status ----------
    def ai_status(self):
        if self.client is None:
            return {"available": False, "reason": "AI is switched off (COHORT_STUDIO_AI=off)."}
        d = self.client.describe()
        if not d["configured"]:
            return {**d, "available": False, "reason": "No API key found. Add OPENAI_API_KEY to .env, or set "
                                                       "OPENAI_BASE_URL to a local model."}
        return {**d, "available": True, "calls": self.ai_calls, "call_limit": self.settings.ai_call_limit,
                "tokens": self.tokens}

    def meta(self):
        return {"ai": self.ai_status(), "presets": self.presets, "advanced": form_options(self.catalog),
                "specimen": self.specimen, "fields": self.fields,
                "storage": self.db.kind,
                "files": {name: ("present" if st["present"] else "missing") for name, st in self.store.file_status.items()},
                "release": {"samples": sum(self.catalog.sample_type_counts.values()),
                            "patients": int(self.store.manifest["patient_id"].nunique()),
                            "primary_tumours": len(self.catalog.pt_ids),
                            "slides": int(len(self.store.slides)),
                            "genes": len(self.catalog.genes),
                            "clinical_fields": len(self.catalog.profiles)}}

    # ---------- paid model calls: limits and accounting ----------
    def _ai_allowed(self, caller):
        """Raise ServiceError(429) if the model may not be called now for this caller."""
        with self._lock:
            if self.ai_calls >= self.settings.ai_call_limit:
                raise ServiceError(f"This server has used its limit of {self.settings.ai_call_limit} model calls. "
                                   "Restart it or raise COHORT_STUDIO_AI_CALL_LIMIT.", 429)
        try:
            self.guard.check(caller)
        except LimitExceeded as exc:
            raise ServiceError(str(exc), 429)

    def _account(self, caller, out):
        with self._lock:
            if not out.get("cached"):
                self.ai_calls += 1
                for k in self.tokens:
                    self.tokens[k] += int((out.get("usage") or {}).get(k, 0) or 0)
        self.guard.record(caller, out.get("usage"), getattr(self.client, "model", ""), cached=bool(out.get("cached")))

    # ---------- the hosted AI service for the CLI and scripts (/api/v1) ----------
    def api_draft(self, caller, request):
        """Draft a request with the server's model, the same way the CLI does with a user's own key (questions
        get their recommended answer, the draft is compared with the built-in parser). Returns the runnable spec."""
        from cohort_studio.llm import spec_from_draft
        request = (request or "").strip()
        if not request:
            raise ServiceError("Send {\"request\": \"...\"} with the cohort in plain English.")
        if len(request) > MAX_QUERY_CHARS:
            raise ServiceError(f"The request is longer than {MAX_QUERY_CHARS} characters.", 413)
        if not self.ai_status()["available"]:
            raise ServiceError("This server has no AI model configured.", 503)
        self._ai_allowed(caller)
        try:
            d = make_draft(request, self.store, self.client, catalog=self.catalog, compare_with_rules=True)
        except AIError as exc:
            raise ServiceError(str(exc), 502)
        self._account(caller, d)
        spec = spec_from_draft(d)
        return {"spec": spec, "problems": [m for _, m in validate(spec, self.store)], "model": self.client.label,
                "limits": self.guard.status(caller)}

    def api_status(self, caller):
        ai = self.ai_status()
        return {"ai": {"available": ai["available"], "model": ai.get("model"), "reason": ai.get("reason")},
                "limits": self.guard.status(caller)}

    # ---------- drafting ----------
    def draft(self, user, query, use_ai=True, refine_search_id=None, current_steps=None, current_request=None, history=None,
              caller=None):
        caller = caller or Caller(identity=f"local:{user}", ip="local")
        query = (query or "").strip()
        if not query:
            raise ServiceError("Type a cohort request first.")
        if len(query) > MAX_QUERY_CHARS:
            raise ServiceError(f"The request is longer than {MAX_QUERY_CHARS} characters.", 413)
        current = None
        if refine_search_id:
            s = self.db.get_search(user, refine_search_id)
            if not s:
                raise ServiceError("That cohort isn't in your history any more.", 404)
            current = s["spec"]
        if current_steps is not None:      # editing in words: the editor's steps are the starting point
            current = {"request": current_request or (current or {}).get("request", ""),
                       "steps": [{k: v for k, v in st.items() if not str(k).startswith("_")}
                                 for st in current_steps if isinstance(st, dict) and not st.get("_off")]}
        limit_note = None
        if use_ai and self.ai_status()["available"]:
            try:
                self._ai_allowed(caller)
            except ServiceError as exc:
                if current:
                    raise
                limit_note = str(exc)              # over a limit: the built-in parser drafts instead, and says why
        if use_ai and self.ai_status()["available"] and limit_note is None:
            try:
                d = make_draft(query, self.store, self.client, current_spec=current, catalog=self.catalog, history=history,
                               compare_with_rules=True, interactive=True)
            except AIError as exc:
                raise ServiceError(str(exc), 502)
            if current:
                d["diff"] = diff_steps(current["steps"], materialize(d))
            self._account(caller, d)
            mode = "ai"
        else:
            if current:
                raise ServiceError("Refining a cohort needs the AI model. Use Advanced search or edit the steps instead.")
            d = draft_from_spec(parse_request(query, self.store), self.store, source="rules")
            if limit_note:
                d["warnings"].append(limit_note)
            mode = "rules"
        return self._save_draft(user, query, d, mode, refine_search_id)

    def advanced_preview(self, form):
        """Live count for the advanced form: build the steps and run them (after the one-click inflation fix)."""
        try:
            spec, _ = build_spec(form, self.store, self.catalog)
        except (FormError, ValueError, TypeError) as exc:
            return {"ok": False, "error": str(exc)}
        ids, r = run_cohort(self.store, spec["request"], spec["steps"])
        if ids is None:
            return {"ok": False, "error": next((s.error for s in r.steps if s.error), "These choices can't run.")}
        m = self.store.manifest
        return {"ok": True, "n_samples": len(ids), "n_patients": int(m.loc[m["sample_id"].isin(ids), "patient_id"].nunique()),
                "steps": [describe_step(s) for s in spec["steps"]]}

    # ---------- comparing cohorts ----------
    def compare(self, user, a, b):
        try:
            return compare(self, user, a, b)
        except CompareError as exc:
            raise ServiceError(str(exc), 404 if "isn't" in str(exc) or "no saved" in str(exc) else 400)

    def compare_insights(self, user, a, b, caller=None):
        caller = caller or Caller(identity=f"local:{user}", ip="local")
        if not self.ai_status()["available"]:
            raise ServiceError("AI insights need a model. Add OPENAI_API_KEY to .env, or point OPENAI_BASE_URL at a local model.")
        self._ai_allowed(caller)
        metrics = metrics_for_model(self.compare(user, a, b))
        try:
            out = ai_insights.generate(self.client, metrics)
        except AIError as exc:
            raise ServiceError(str(exc), 502)
        self._account(caller, out)
        return out

    def advanced(self, user, form):
        try:
            spec, corrections = build_spec(form, self.store, self.catalog)
        except (FormError, ValueError, TypeError) as exc:
            raise ServiceError(str(exc))
        d = draft_from_spec(spec, self.store, source="advanced")
        d["corrections"] = corrections
        return self._save_draft(user, spec["request"], d, "advanced", None)

    def _save_draft(self, user, query, d, mode, refine_search_id):
        d["mode"] = mode
        d["refine_search_id"] = refine_search_id
        draft_id = self.db.save_draft(user, query, d)
        out = self._draft_view(d)
        out["draft_id"] = draft_id
        return out

    @staticmethod
    def _draft_view(d):
        out = {k: v for k, v in d.items() if k not in ("prebuilt",)}
        if not d["clarifications"]:
            spec = to_spec(d)
            out["spec"] = spec
            out["spec_labels"] = [describe_step(s) for s in spec["steps"]]
        return out

    def resolve(self, user, draft_id, answers):
        d = self.db.get_draft(user, draft_id)
        if d is None:
            raise ServiceError("That draft has expired. Search again.", 404)
        valid = {q["id"]: q for q in d["clarifications"]}
        clean = {}
        for qid, i in (answers or {}).items():
            if qid in valid and str(i).isdigit() and int(i) < len(valid[qid]["options"]):
                clean[qid] = int(i)
        missing = [q["question"] for q in d["clarifications"] if q["id"] not in clean]
        if missing:
            raise ServiceError("Please answer: " + " / ".join(missing))
        spec = to_spec(d, clean)
        return {"spec": spec, "spec_labels": [describe_step(s) for s in spec["steps"]], "mode": d.get("mode", "ai"),
                "refine_search_id": d.get("refine_search_id")}

    # ---------- running ----------
    def run(self, user, spec, query=None, mode="ai", draft_id=None, search_id=None, edited=False, source=None, note=""):
        spec = copy.deepcopy(spec or {})
        if not spec.get("steps"):
            raise ServiceError("There are no steps to run.")
        rechecked = []
        if edited and spec.get("parser") != "rules":
            steps, _, rechecked, problems = self._prepare_edited(spec["steps"])
            if problems:
                raise ServiceError("These steps can't run on this release: " + "; ".join(p["message"] for p in problems))
            spec["steps"] = steps
        payload = self._evaluate(spec)
        payload["rechecked"] = rechecked
        summary = self._summary(payload)
        query = query or spec.get("request", "")
        existing = self.db.get_search(user, search_id) if search_id else None
        if existing:
            self._ensure_first_version(user, existing)
            self.db.update_search(user, search_id, spec, summary)
        else:
            search_id = self.db.add_search(user, query, mode, spec, summary, draft_id)
        latest = self.db.list_versions(user, search_id)
        if not latest or not same_steps(self.db.get_version(user, search_id, latest[-1]["n"])["spec"]["steps"], spec["steps"]):
            self.db.add_version(user, search_id, spec, summary, source or ("draft" if not existing else "edit"),
                                note or ("First run" if not existing else ""))
        payload.update(search_id=search_id, query=query, mode=mode, versions=self.db.list_versions(user, search_id))
        return payload

    def open_search(self, user, search_id):
        s = self._search(user, search_id)
        self._ensure_first_version(user, s)
        payload = self._evaluate(s["spec"])
        payload.update(search_id=search_id, query=s["query"], mode=s["mode"], rechecked=[],
                       versions=self.db.list_versions(user, search_id))
        return payload

    def fix(self, user, search_id, step_index=None):
        s = self._search(user, search_id)
        result = engine.run(s["spec"], self.store)
        if not any(st.inflated for st in result.steps):
            raise ServiceError("No step counts samples twice, so there is nothing to fix.")
        spec, changes = engine.apply_fixes(s["spec"], result, only=step_index)
        return self.run(user, spec, s["query"], s["mode"], search_id=search_id, source="fix",
                        note="; ".join(changes) or "Fixed double counting")

    # ---------- editing a built cohort ----------
    def _prepare_edited(self, steps):
        """Check edited steps one at a time (keeping the user's order and each step's client id `_cid`), drop the
        ones switched off (`_off`), and add any join a filter needs. Returns (steps, ids, fixes, problems)."""
        out, ids, fixes, problems = [], [], [], []
        for raw in steps or []:
            if not isinstance(raw, dict) or raw.get("_off"):
                continue
            cid = raw.get("_cid")
            clean = {k: v for k, v in raw.items() if not str(k).startswith("_")}
            chk = Checker(self.store, self.catalog, allow_questions=False)
            checked = chk.check_steps([clean])
            fixes += chk.corrections
            if chk.unsupported:
                problems += [{"cid": cid, "message": u["reason"]} for u in chk.unsupported]
                continue
            if checked:
                out.append(checked[0])
                ids.append(cid)
        # No sample-type step is a choice (switched off or deleted): the cohort then starts from every sample.
        steps2, ids2 = ensure_joins(out, ids)
        return steps2, ids2, fixes, problems

    def preview(self, steps, base_steps=None, impacts=True):
        """Live counts for the step editor: every step's samples in and out, and what the cohort would be without it."""
        run_steps, ids, fixes, problems = self._prepare_edited(steps)
        out = {"ok": False, "steps": [], "final": None, "impacts": {}, "fixes": fixes, "problems": problems, "changes": []}
        if base_steps is not None:
            out["changes"] = diff_steps([s for s in base_steps if not s.get("_off")], run_steps)
        if not run_steps:
            return out
        spec = {"request": "preview", "steps": run_steps}
        problems_v = validate(spec, self.store)
        r = engine.run(spec, self.store)
        m = self.store.manifest
        out["steps"] = [{"cid": cid, "label": s.label, "op": s.op, "samples_in": s.samples_in, "samples": s.samples_out,
                         "patients": s.patients_out, "rows": s.rows_out, "removed": len(s.removed),
                         "removed_reason": s.removed_reason, "inflated": s.inflated, "error": s.error, "skipped": s.skipped}
                        for cid, s in zip(ids, r.steps)]
        out["problems"] += [{"cid": ids[i] if i < len(ids) else None, "message": msg} for i, msg in problems_v]
        out["final"] = {"samples": len(r.cohort_ids), "rows": r.final_rows, "inflated": r.unresolved_inflation,
                        "patients": int(m.loc[m["sample_id"].isin(r.cohort_ids), "patient_id"].nunique())}
        out["ok"] = r.ok
        if impacts and r.ok and len(run_steps) <= 14:
            final = len(r.cohort_ids)
            for i, cid in enumerate(ids):
                if cid is None or run_steps[i]["op"] == "sample_type":
                    continue
                without = run_steps[:i] + run_steps[i + 1:]
                if validate({"steps": without}, self.store):
                    continue
                r2 = engine.run({"request": "impact", "steps": without}, self.store)
                if r2.ok:
                    out["impacts"][str(cid)] = len(r2.cohort_ids) - final
        return out

    def _ensure_first_version(self, user, search):
        if not self.db.list_versions(user, search["id"]):
            self.db.add_version(user, search["id"], search["spec"],
                                {"n_samples": search["n_samples"], "n_patients": search["n_patients"], "verdict": search["verdict"]},
                                "draft", "First run")

    def versions(self, user, search_id):
        s = self._search(user, search_id)
        self._ensure_first_version(user, s)
        return {"search_id": search_id, "query": s["query"], "versions": self.db.list_versions(user, search_id)}

    def version(self, user, search_id, n):
        v = self.db.get_version(user, search_id, n)
        if not v:
            raise ServiceError(f"Version {n} isn't saved.", 404)
        v["labels"] = [describe_step(st) for st in v["spec"]["steps"]]
        return v

    def save_version(self, user, search_id, steps, note="", source="edit"):
        """Run edited steps as the next version of a saved cohort."""
        s = self._search(user, search_id)
        spec = {k: v for k, v in s["spec"].items() if k not in ("steps", "fix_log")}
        spec["steps"] = steps
        spec.setdefault("edits", []).append({"source": source, "note": note, "changes": diff_steps(s["spec"]["steps"], [
            x for x in steps if not x.get("_off")])})
        return self.run(user, spec, s["query"], s["mode"], search_id=search_id, edited=True, source=source, note=note)

    def fork(self, user, steps, query, base_search_id=None, note=""):
        """Run edited steps as a brand-new cohort (the original stays as it was)."""
        base = self._search(user, base_search_id)["spec"] if base_search_id else {}
        spec = {k: v for k, v in base.items() if k not in ("steps", "fix_log", "edits")}
        spec.update(steps=steps, request=query or base.get("request", "Edited cohort"))
        spec.setdefault("parser", "edit")
        return self.run(user, spec, query, "edit", edited=True, source="draft", note=note or "Saved as a new cohort")

    def field_catalog(self):
        """Every field the step editor can filter on, with its kind and real values."""
        out = []
        for (table, field), prof in self.catalog.profiles.items():
            if table == "survival":
                kind = "time" if field.endswith("_time") else "event" if field in ("os", "pfi", "dss", "dfi") else "categorical"
            elif field == "stage":
                kind = "stage"
            else:
                kind = "numeric" if prof["numeric"] else "categorical"
            group = "Outcome · curated survival" if table == "survival" else FIELD_GROUPS.get(field, "Other clinical fields")
            out.append({"table": table, "field": field, "label": self.catalog.label(table, field), "group": group, "kind": kind,
                        "range": prof["numeric"], "values": [{"value": v["value"], "count": v["count"], "flag": v["flag"]}
                                                             for v in prof["values"][:60]]})
        order = {g: i for i, g in enumerate(FIELD_GROUP_ORDER)}
        return sorted(out, key=lambda f: (order.get(f["group"], 99), f["label"].lower()))

    def confirm_unparsed(self, user, search_id):
        s = self._search(user, search_id)
        spec = s["spec"]
        spec["unparsed_confirmed"] = True
        return self.run(user, spec, s["query"], s["mode"], search_id=search_id)

    def export(self, user, search_id, name):
        if name not in FILES:
            raise ServiceError(f"Unknown file '{name}'.", 404)
        s = self._search(user, search_id)
        spec = s["spec"]
        result = engine.run(spec, self.store)
        if not result.ok:
            raise ServiceError("Fix the steps that didn't run before exporting.", 409)
        if result.unresolved_inflation:
            raise ServiceError("Export is paused: the cohort still counts some samples more than once. Apply the fix first.", 409)
        v = self._verdict(result)
        content = {
            "cohort_samples.csv": lambda: exports.cohort_frame(result).to_csv(index=False),
            "reproduce_cohort.py": lambda: exports.reproduce_script(result, self.store),
            "cohort_spec.json": lambda: json.dumps(spec, indent=2),
            "findings.json": lambda: json.dumps(exports.findings_document(result, v, self.store, "Cohort Studio team"), indent=2),
            "verdict.json": lambda: json.dumps(v.to_dict(), indent=2),
            "ledger.csv": lambda: exports.ledger_frame(result).to_csv(index=False),
            "exclusions.csv": lambda: exports.exclusions_frame(result).to_csv(index=False),
            "sample_selection.csv": lambda: exports.selection_frame(result).to_csv(index=False),
            "coverage.csv": lambda: exports.coverage_frame(result, self.store).to_csv(index=False),
        }[name]()
        return content, FILES[name]

    def _search(self, user, search_id):
        s = self.db.get_search(user, search_id)
        if not s:
            raise ServiceError("That cohort isn't in your history any more.", 404)
        return s

    def _verdict(self, result):
        v = verdict.evaluate(result, self.store)
        extra = [x for x in review_items(result.spec) if x not in v.human_review]
        v.human_review += extra
        return v

    def _evaluate(self, spec):
        result = engine.run(spec, self.store)
        v = self._verdict(result)
        steps = []
        for s in result.steps:
            fix_text = engine.suggest_fix(spec["steps"][s.index])[1] if s.inflated else ""
            shift = sorted(({"characteristic": k, **info} for k, info in s.shift.items() if k != "Sample type"),
                           key=lambda x: -x["tvd"])[:4]
            steps.append({"index": s.index, "label": s.label, "op": s.op, "rows_in": s.rows_in, "rows": s.rows_out,
                          "samples_in": s.samples_in, "samples": s.samples_out, "patients": s.patients_out,
                          "removed": len(s.removed), "removed_reason": s.removed_reason,
                          "removed_examples": s.removed[:6], "inflated": s.inflated,
                          "inflation_note": s.inflation_note, "duplicate_examples": s.duplicate_examples[:5],
                          "fix": fix_text, "shift": shift, "error": s.error, "skipped": s.skipped,
                          "missing_fraction": round(s.missing_fraction, 3), "column": s.column,
                          "selection": {"patients": len(s.selection), "examples": s.selection[:4]} if s.selection else None})
        exportable = result.ok and not result.unresolved_inflation
        block = ("" if exportable else "Fix the steps that didn't run first." if not result.ok
                 else "The cohort still counts some samples more than once. Apply the fix first.")
        overall = sorted(({"characteristic": k, **info} for k, info in result.overall_shift.items() if k != "Sample type"),
                         key=lambda x: -x["tvd"])
        # Who is in the cohort after every step (for the funnel), and who each step removed (for the flow diagram).
        comp = self.store.composition_frame()
        counts = lambda ids, col: {str(k): int(n) for k, n in comp.loc[comp.index.intersection(list(ids)), col].value_counts().items()}  # noqa: E731
        stages, ids = [set(result.start_samples)], set(result.start_samples)
        for s in result.steps:
            if not s.skipped:
                ids = ids - set(s.removed)
            stages.append(ids)
        chars = [c for c in FUNNEL_CHARACTERISTICS if c in comp.columns]
        composition = {c: {"start": counts(stages[0], c), "steps": [counts(x, c) for x in stages[1:]],
                           "final": counts(result.cohort_ids, c)} for c in chars}
        for st, s in zip(steps, result.steps):
            st["removed_by"] = {c: counts(s.removed, c) for c in ("PAM50 subtype", "ER status (IHC)", "Sample type") if c in chars} if s.removed else {}
        return {"spec": spec, "spec_labels": [describe_step(x) for x in spec["steps"]], "composition": composition,
                "start": {"samples": len(result.start_samples)},
                "final": {"rows": result.final_rows, "samples": len(result.cohort_ids),
                          "patients": int(self.store.manifest.loc[self.store.manifest["sample_id"].isin(result.cohort_ids),
                                                                  "patient_id"].nunique())},
                "steps": steps, "overall_shift": overall, "verdict": v.to_dict(), "shift_threshold": verdict.SHIFT_WARN,
                "exportable": exportable, "export_block_reason": block, "files": list(FILES),
                "unparsed": spec.get("unparsed", []), "unparsed_confirmed": bool(spec.get("unparsed_confirmed")),
                "notes": spec.get("notes", []), "assistant": spec.get("assistant"),
                "cohort_ids": list(result.cohort_ids)}

    @staticmethod
    def _summary(payload):
        return {"n_samples": payload["final"]["samples"], "n_patients": payload["final"]["patients"],
                "verdict": payload["verdict"]["verdict"], "reason": payload["verdict"]["reason"][:500]}

    # ---------- memory ----------
    def history(self, user, limit=30):
        return self.db.list_searches(user, limit)

    def delete_search(self, user, search_id):
        if not self.db.delete_search(user, search_id):
            raise ServiceError("Not found.", 404)
        return {"deleted": search_id}

    def favorites(self, user):
        return self.db.list_favorites(user)

    def add_favorite(self, user, query=None, label=None, search_id=None):
        spec = None
        if search_id:
            s = self._search(user, search_id)
            query, spec = s["query"], s["spec"]
        if not query:
            raise ServiceError("Nothing to save.")
        return {"id": self.db.add_favorite(user, (label or query)[:120], query, spec, search_id)}

    def delete_favorite(self, user, fav_id):
        if not self.db.delete_favorite(user, fav_id):
            raise ServiceError("Not found.", 404)
        return {"deleted": fav_id}

    def run_favorite(self, user, fav_id, caller=None):
        f = self.db.get_favorite(user, fav_id)
        if not f:
            raise ServiceError("Not found.", 404)
        if f["spec"]:
            return {"kind": "result", "result": self.run(user, f["spec"], f["query"], mode="favourite")}
        return {"kind": "draft", "draft": self.draft(user, f["query"], caller=caller)}

    def suggest(self, user, text):
        recent = self.db.recent_queries(user) + [f["query"] for f in self.db.list_favorites(user)]
        return self.suggester.suggest(text, recent)
