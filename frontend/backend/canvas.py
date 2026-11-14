"""The visual canvas's backend: what the palette shows, live evaluation, the SQL view, one patient's tree and the
cohort's real slide images.

Everything reuses the engine and the service: the canvas sends the same step list the step editor sends, and gets
back the same ledger, verdict and composition, plus a few things only a picture needs (each step's grain, the fix a
join would take, the SQL with each step's lines, a gallery of the cohort's slides).

Slide images: NCI Imaging Data Commons serves every TCGA slide as DICOM. Each slide series has a small THUMBNAIL
image; it is fetched once through IDC's public DICOMweb endpoint, cropped to the tissue and cached on disk, so a
slide is never requested twice. The data is CC BY 3.0 (credit the TCGA Research Network and NCI IDC).
"""
import copy
import io
import json
import pathlib
import re
import time
import urllib.request

import pandas as pd

from cohort_studio import engine, exports, ops
from cohort_studio import sql as sqlview
from cohort_studio.assistant.catalog import NOT_IN_RELEASE
from cohort_studio.assistant.verify import canonicalize
from cohort_studio.spec import LAYER_LABELS

from .config import FRONTEND
from .service import PRESETS, ServiceError, _f, _JC, _PT

IDC_DICOMWEB = ("https://proxy.imaging.datacommons.cancer.gov/current/"
                "viewer-only-no-downloads-see-tinyurl-dot-com-slash-3j3d9jyp/dicomWeb")
THUMB_DIR = FRONTEND / ".data" / "thumbs"
BARCODE = re.compile(r"^TCGA-[A-Z0-9]{2}-[A-Z0-9]{4}-[0-9A-Z-]{3,30}$")
KIND_SHORT = {"diagnostic (FFPE)": "DX", "frozen, top section": "TS", "frozen, middle section": "MS", "frozen, bottom section": "BS"}


def _stats(counts):
    """mean / max / share > 1 of a per-key count Series."""
    if not len(counts):
        return {"mean": 0, "max": 0, "multi": 0}
    return {"mean": round(float(counts.mean()), 2), "max": int(counts.max()), "multi": round(float((counts > 1).mean()), 3)}


def _grain(rows, samples, patients):
    """What one row stands for after a step: several rows per sample, one per sample, or one per patient."""
    if samples and rows > samples:
        return "multi"
    if samples and samples == patients:
        return "patient"
    return "sample"


class Canvas:
    def __init__(self, svc):
        self.svc, self.store = svc, svc.store
        self._slides_full = None
        self._thumb_fail = {}
        self._catalog = None

    # ---------- the palette ----------
    def catalog(self):
        if self._catalog is not None:
            return self._catalog
        s, m = self.store, self.store.manifest
        slides = s.slides
        per_patient = m.groupby("patient_id").size()
        tables = [{
            "id": "samples", "label": "Samples", "grain": "sample", "rows": int(len(m)), "samples": int(m["sample_id"].nunique()),
            "patients": int(m["patient_id"].nunique()), "keys": ["sample_id", "patient_id"],
            "per_patient": _stats(per_patient),
            "about": "The release's samples: tumours, normal tissue and metastases. Every cohort starts here."}, {
            "id": "slides", "label": "Pathology slides", "grain": "slide", "rows": int(len(slides)),
            "samples": int(slides["sample_id"].nunique()), "patients": int(slides["patient_id"].nunique()),
            "keys": ["sample_id", "patient_id"], "per_sample": _stats(slides.groupby("sample_id").size()),
            "kinds": [{"value": k, "short": KIND_SHORT.get(k, k), "count": int(n)} for k, n in slides["slide_kind"].value_counts().items()],
            "about": "Several slides per sample (diagnostic FFPE and frozen sections), so joining one row per slide "
                     "counts samples more than once."}]
        for name, label, about in (("clinical", "Clinical records", "One clinical record per sample: receptors, "
                                    "subtype, stage, age, treatment."),
                                   ("survival", "Curated survival", "TCGA-CDR survival: OS, PFI, DSS, DFI with times.")):
            if not s.available(name):
                continue
            t = s.table(name)
            tables.append({"id": name, "label": label, "grain": "sample", "rows": int(len(t)), "samples": int(t["sample_id"].nunique()),
                           "patients": int(t["patient_id"].nunique()), "keys": ["sample_id", "patient_id"],
                           "per_sample": _stats(t.groupby("sample_id").size()),
                           "per_patient": _stats(t.groupby("patient_id").size()), "about": about})
        normals = ops.normal_samples(m)
        tables.append({"id": "normals", "label": "Matched normal tissue", "grain": "sample", "rows": int(len(normals)),
                       "samples": int(len(normals)), "patients": int(normals["patient_id"].nunique()), "keys": ["patient_id"],
                       "per_patient": _stats(normals.groupby("patient_id").size()),
                       "about": "Normal-tissue samples, matched to a tumour by the patient barcode."})
        cat = self.svc.catalog
        self._catalog = {
            "tables": tables,
            "layers": [{"value": k, "label": LAYER_LABELS.get(k, k), "count": int(n), "share": round(n / max(1, len(m)), 3)}
                       for k, n in cat.layer_counts.items()],
            "sample_types": [{"value": t, "count": int(cat.sample_type_counts.get(t, 0))} for t in ops.SAMPLE_TYPES],
            "genes": [{"value": g, "count": int(n)} for g, n in cat.gene_counts.items()],
            "templates": self.templates(),
            "not_in_release": [what for _, what in NOT_IN_RELEASE],
            "sql": {"available": sqlview.available()},
            "images": {"source": "NCI Imaging Data Commons", "license": "CC BY 3.0"},
        }
        return self._catalog

    def genes(self, q, limit=8):
        """A gene symbol typed in the block library: is it in the mutation file, what starts with it, or what was meant."""
        cat = self.svc.catalog
        q = re.sub(r"[^A-Z0-9-]", "", str(q or "").upper())[:20]
        if not q or not cat.genes:
            return {"query": q, "exact": False, "matches": [], "closest": []}
        exact = q in cat.genes
        names = ([q] if exact else []) + sorted(g for g in cat.genes if g.startswith(q) and g != q)[:limit - int(exact)]
        return {"query": q, "exact": exact, "matches": [{"value": g, "count": cat.gene_mutated_count(g)} for g in names],
                "closest": [] if names else cat.closest_genes(q)}

    def templates(self):
        out = []
        for p in PRESETS:
            steps = p["steps"]
            if steps == "node_positive":
                prof = self.svc.catalog.profile("clinical", "pathologic_N")
                vals = [v["value"] for v in (prof or {}).get("values", []) if v["value"].lower().startswith(("n1", "n2", "n3"))]
                steps = [_PT, _JC, _f("pam50", "in", ["LumA"]), _f("pathologic_N", "in", vals),
                         {"op": "join", "table": "slides", "key": "sample_id", "mode": "exists", "slide_kinds": ["diagnostic (FFPE)"]}]
            if not steps:
                continue
            demo = copy.deepcopy(steps)
            if p["label"] == "The double-count demo":     # the canvas shows the inflation first, then the fix
                demo[-1]["mode"] = "rows"
            out.append({"label": p["label"], "tag": p["tag"], "description": p["description"], "query": p["query"],
                        "steps": canonicalize(demo)[0]})
        out.append({"label": "Complete multi-omics", "tag": "Layers", "query": "primary tumours with every data layer",
                    "description": "Primary tumours with RNA, protein, mutation and copy-number data and survival.",
                    "steps": [_PT] + [{"op": "layer", "layer": l} for l in ("rna", "protein", "mutation", "copy_number", "survival")]})
        return out

    # ---------- live evaluation ----------
    def _run_steps(self, steps):
        run_steps, ids, fixes, problems = self.svc._prepare_edited(steps)
        return run_steps, ids, fixes, problems

    def evaluate(self, steps):
        run_steps, ids, fixes, problems = self._run_steps(steps)
        m = self.store.manifest
        start = {"rows": int(len(m)), "samples": int(m["sample_id"].nunique()), "patients": int(m["patient_id"].nunique())}
        start["grain"] = _grain(start["rows"], start["samples"], start["patients"])
        base = {"ok": False, "start": start, "steps": [], "fixes": fixes, "problems": problems, "run_steps": [],
                "final": dict(start), "verdict": None, "composition": {}, "empty": not run_steps}
        if not run_steps:
            return base
        spec = {"request": "canvas", "steps": run_steps}
        payload = self.svc._evaluate(spec)
        out_steps = []
        for st, cid, raw in zip(payload["steps"], ids, run_steps):
            st = {k: v for k, v in st.items() if k not in ("removed_examples",)}
            st["cid"] = cid
            st["grain"] = _grain(st["rows"], st["samples"], st["patients"]) if not st["error"] else None
            if st["inflated"]:
                fixed, why = engine.suggest_fix(raw)
                st["fix_step"], st["fix"] = fixed, why
            out_steps.append(st)
        final = payload["final"]
        final["grain"] = _grain(final["rows"], final["samples"], final["patients"])
        v = payload["verdict"]
        return {"ok": not any(st["error"] for st in out_steps), "start": start, "steps": out_steps, "final": final,
                "run_steps": [{**s, "_cid": c} for s, c in zip(run_steps, ids)], "fixes": fixes, "problems": problems,
                "verdict": {"verdict": v["verdict"], "reason": v["reason"], "checks": v["checks"],
                            "human_review": v.get("human_review", []), "limitations": v.get("limitations", [])},
                "composition": payload["composition"], "overall_shift": payload["overall_shift"],
                "exportable": payload["exportable"], "export_block_reason": payload["export_block_reason"],
                "shift_threshold": payload["shift_threshold"], "cohort_ids": payload["cohort_ids"]}

    # ---------- SQL, Python and JSON views ----------
    def code(self, steps, verify=True):
        run_steps, ids, _, problems = self._run_steps(steps)
        if not run_steps:
            return {"sql": "-- Add a step to see the cohort as SQL.\nSELECT sample_id FROM manifest;", "lines": {}, "verified": None,
                    "python": "", "json": json.dumps({"steps": []}, indent=2)}
        spec = {"request": "canvas", "steps": run_steps}
        result = engine.run(spec, self.store)
        out = {"json": json.dumps(spec, indent=2), "verified": None, "lines": {}, "cids": ids}
        try:
            plan = sqlview.to_sql(spec, self.store)
            out["sql"] = plan.text()
            out["lines"] = {str(ids[i]) if ids[i] is not None else f"auto-{i}": span for i, span in plan.line_map().items()
                            if i < len(ids)}
            if verify:
                out["verified"] = sqlview.verify(plan, result)
        except Exception as exc:           # a step SQL can't express: say so instead of showing wrong SQL
            out["sql"] = f"-- This cohort can't be written as SQL yet: {exc}"
            out["verified"] = {"ok": None, "reason": str(exc)}
        try:
            out["python"] = exports.reproduce_script(result, self.store) if result.ok else \
                "# Fix the steps that don't run, then the reproduce script appears here."
        except Exception as exc:
            out["python"] = f"# The reproduce script couldn't be written: {exc}"
        return out

    # ---------- one patient, as a tree ----------
    def _slides(self):
        if self._slides_full is None:
            self._slides_full = pd.read_csv(pathlib.Path(self.store.data_dir) / "slide_manifest.csv", dtype=str).fillna("")
        return self._slides_full

    def patient(self, patient_id):
        pid = str(patient_id or "").strip()[:12]
        m = self.store.manifest
        rows = m[m["patient_id"] == pid]
        if rows.empty:
            raise ServiceError("That patient isn't in this release.", 404)
        comp = self.store.composition_frame()
        slides = self._slides()
        samples = []
        for _, r in rows.sort_values("sample_id").iterrows():
            sid = r["sample_id"]
            c = comp.loc[sid] if sid in comp.index else {}
            sl = slides[slides["sample_id"] == sid]
            samples.append({
                "sample_id": sid, "sample_type": r["sample_type"],
                "layers": {k: bool(r[col]) for k, col in ops.LAYER_COLUMNS.items()},
                "pam50": c.get("PAM50 subtype") if len(c) else None, "er": c.get("ER status (IHC)") if len(c) else None,
                "stage": c.get("Stage") if len(c) else None, "age": c.get("Age band") if len(c) else None,
                "slides": [{"slide_barcode": x["slide_barcode"], "slide_kind": x["slide_kind"],
                            "short": KIND_SHORT.get(x["slide_kind"], ""), "viewer_url": x.get("viewer_url", ""),
                            "image": bool(x.get("series_uid"))} for _, x in sl.sort_values("slide_barcode").iterrows()]})
        return {"patient_id": pid, "site": pid[5:7], "samples": samples}

    # ---------- the cohort's slides ----------
    def gallery(self, steps, limit=24, kinds=None):
        run_steps, _, _, _ = self._run_steps(steps)
        spec = {"request": "canvas", "steps": run_steps}
        r = engine.run(spec, self.store) if run_steps else None
        ids = set(r.cohort_ids) if r is not None and r.ok else set(self.store.manifest["sample_id"])
        if not kinds:
            for st in run_steps:
                if st.get("op") == "join" and st.get("table") == "slides" and st.get("slide_kinds"):
                    kinds = st["slide_kinds"]
        slides = self._slides()
        pool = slides[slides["sample_id"].isin(ids)]
        by_kind = {k: int(n) for k, n in pool["slide_kind"].value_counts().items()}
        if kinds:
            pool = pool[pool["slide_kind"].isin(kinds)]
        pool = pool.sort_values("slide_barcode")
        n = len(pool)
        limit = max(1, min(int(limit or 24), 60))
        step = max(1, n // limit) if n else 1
        pick = pool.iloc[::step].head(limit)
        comp = self.store.composition_frame()
        items = [{"slide_barcode": x["slide_barcode"], "sample_id": x["sample_id"], "patient_id": x["patient_id"],
                  "slide_kind": x["slide_kind"], "short": KIND_SHORT.get(x["slide_kind"], ""), "viewer_url": x["viewer_url"],
                  "pam50": comp.loc[x["sample_id"], "PAM50 subtype"] if "PAM50 subtype" in comp.columns and x["sample_id"] in comp.index else None,
                  "image": bool(x["series_uid"])}
                 for _, x in pick.iterrows()]
        return {"total": int(n), "samples_with_slides": int(pool["sample_id"].nunique()), "cohort_samples": len(ids),
                "kinds": kinds or [], "by_kind": by_kind, "items": items}

    # ---------- one slide's thumbnail (IDC DICOMweb, cached) ----------
    def thumb(self, barcode):
        barcode = str(barcode or "").strip()
        if not BARCODE.match(barcode):
            raise ServiceError("That isn't a slide barcode.", 400)
        path = THUMB_DIR / f"{barcode}.jpg"
        if path.exists():
            return path.read_bytes()
        failed = self._thumb_fail.get(barcode)
        if failed and time.time() - failed < 600:
            raise ServiceError("This slide's image isn't available right now.", 404)
        row = self._slides()
        row = row[row["slide_barcode"] == barcode]
        if row.empty or not row.iloc[0]["series_uid"]:
            raise ServiceError("No image is recorded for this slide.", 404)
        study, series = row.iloc[0]["study_uid"], row.iloc[0]["series_uid"]
        try:
            data = self._fetch_thumb(study, series)
        except Exception:
            self._thumb_fail[barcode] = time.time()
            raise ServiceError("Imaging Data Commons didn't return this slide's image.", 502)
        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
        return data

    @staticmethod
    def _get(url, accept):
        req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "cohort-studio"})
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.read()

    def _fetch_thumb(self, study, series):
        base = f"{IDC_DICOMWEB}/studies/{study}/series/{series}"
        listing = json.loads(self._get(f"{base}/instances?includefield=00080008&includefield=00480006&includefield=00480007",
                                       "application/dicom+json"))
        thumbs = [x for x in listing if "THUMBNAIL" in (x.get("00080008", {}).get("Value") or [])]
        if not thumbs:
            raise ValueError("no thumbnail instance")
        uid = thumbs[0]["00080018"]["Value"][0]
        raw = self._get(f"{base}/instances/{uid}/frames/1/rendered", "image/jpeg")
        return _crop_to_tissue(raw)


def _crop_to_tissue(raw, max_w=720):
    """Trim the glass around the tissue and scale down, so a card shows the specimen, not white space.
    Without Pillow (it's optional for the web app) the thumbnail is served as IDC rendered it."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return raw
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    grey = ImageOps.invert(im.convert("L")).point(lambda p: 255 if p > 28 else 0)
    box = grey.getbbox()
    if box:
        pad = 14
        box = (max(0, box[0] - pad), max(0, box[1] - pad), min(im.width, box[2] + pad), min(im.height, box[3] + pad))
        if (box[2] - box[0]) * (box[3] - box[1]) > 0.04 * im.width * im.height:
            im = im.crop(box)
    if im.width > max_w:
        im = im.resize((max_w, max(1, round(im.height * max_w / im.width))), Image.LANCZOS)
    out = io.BytesIO()
    im.save(out, "JPEG", quality=84, optimize=True, progressive=True)
    return out.getvalue()
