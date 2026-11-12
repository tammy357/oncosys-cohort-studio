"""Compare two saved cohorts. Every number comes from the cohort engine on the frozen files.

overlap        who is in both, only one, Jaccard index, shared patients
composition    the mix of PAM50 subtype, ER status, stage, age band, sample type and submitting site in each,
               with the total variation distance (TVD) between the two
coverage       share of each cohort with each data layer and a diagnostic slide
outcomes       Kaplan-Meier curves for overall survival and the progression-free interval (descriptive only),
               with a log-rank test when the cohorts share no patients
protocol       which steps both cohorts share and which belong to one
verdicts       both verdicts and the checks that differ
summary        plain-sentence findings computed from the above (no model involved)
"""
import json
import math

import pandas as pd

from cohort_studio import engine
from cohort_studio.spec import describe_step

CHARACTERISTICS = ["PAM50 subtype", "ER status (IHC)", "Stage", "Age band", "Sample type", "Submitting site"]
LAYERS = [("rna", "RNA expression"), ("protein", "Protein (RPPA)"), ("mutation", "Mutation calls (MC3)"),
          ("copy_number", "Copy number"), ("survival", "Curated survival"), ("clinical", "Clinical record")]
LAYER_COL = {"rna": "has_rna", "protein": "has_protein", "mutation": "has_mutation", "copy_number": "has_copy_number",
             "survival": "has_survival", "clinical": "in_clinical"}


class CompareError(ValueError):
    pass


# ---------- survival statistics ----------
def km_curve(times, events, max_points=160):
    """Kaplan-Meier estimate. times in days, events 1/0. Returns points in years plus summary numbers."""
    df = pd.DataFrame({"t": pd.to_numeric(times, errors="coerce"), "e": pd.to_numeric(events, errors="coerce")}).dropna()
    df = df[df["t"] >= 0]
    n = len(df)
    if n == 0:
        return None
    surv, at_risk, median = 1.0, n, None
    points = [[0.0, 1.0]]
    for t, grp in df.groupby("t", sort=True):
        d = int(grp["e"].sum())
        if d:
            surv *= 1 - d / at_risk
            points.append([round(t / 365.25, 4), round(surv, 4)])
            if median is None and surv <= 0.5:
                median = t / 365.25
        at_risk -= len(grp)
    last = float(df["t"].max()) / 365.25
    points.append([round(last, 4), round(surv, 4)])
    if len(points) > max_points:
        step = len(points) / max_points
        points = [points[int(i * step)] for i in range(max_points)] + [points[-1]]
    return {"n": n, "events": int(df["e"].sum()), "event_pct": round(100 * df["e"].mean(), 1),
            "median_years": None if median is None else round(median, 2), "max_years": round(last, 2),
            "median_followup_years": round(float(df["t"].median()) / 365.25, 2), "points": points}


def logrank(t1, e1, t2, e2):
    """Two-group log-rank test. Returns (chi-square, p) or (None, None) when it can't be computed."""
    a = pd.DataFrame({"t": t1, "e": e1, "g": 0})
    b = pd.DataFrame({"t": t2, "e": e2, "g": 1})
    df = pd.concat([a, b]).apply(pd.to_numeric, errors="coerce").dropna()
    if df.empty or df["g"].nunique() < 2:
        return None, None
    o_minus_e, var = 0.0, 0.0
    for t in sorted(df.loc[df["e"] == 1, "t"].unique()):
        at_risk = df[df["t"] >= t]
        n, n1 = len(at_risk), int((at_risk["g"] == 0).sum())
        d = int(((df["t"] == t) & (df["e"] == 1)).sum())
        d1 = int(((df["t"] == t) & (df["e"] == 1) & (df["g"] == 0)).sum())
        if n < 2:
            continue
        o_minus_e += d1 - d * n1 / n
        var += d * (n1 / n) * (1 - n1 / n) * (n - d) / (n - 1)
    if var <= 0:
        return None, None
    chi2 = o_minus_e ** 2 / var
    return round(chi2, 3), float(math.erfc(math.sqrt(chi2 / 2)))


# ---------- the comparison ----------
def resolve(svc, user, ref):
    """'s:<search id>' (a saved cohort), 'f:<favourite id>' or 'v:<search id>:<version>' -> label, query and spec."""
    kind, _, raw = str(ref or "").partition(":")
    if kind == "v":
        sid, _, n = raw.partition(":")
        if not (sid.isdigit() and n.isdigit()):
            raise CompareError(f"'{ref}' is not a cohort reference.")
        s = svc.db.get_search(user, int(sid))
        v = svc.db.get_version(user, int(sid), int(n)) if s else None
        if not v:
            raise CompareError("That version isn't saved any more.")
        return {"ref": ref, "kind": "version", "id": s["id"], "label": f"{s['query']} · v{v['n']}", "mode": s["mode"], "spec": v["spec"]}
    if not raw.isdigit():
        raise CompareError(f"'{ref}' is not a cohort reference.")
    if kind == "s":
        s = svc.db.get_search(user, int(raw))
        if not s:
            raise CompareError("That cohort isn't in your history any more.")
        return {"ref": ref, "kind": "search", "id": s["id"], "label": s["query"], "mode": s["mode"], "spec": s["spec"]}
    if kind == "f":
        f = svc.db.get_favorite(user, int(raw))
        if not f or not f.get("spec"):
            raise CompareError("That favourite has no saved steps to compare.")
        return {"ref": ref, "kind": "favourite", "id": f["id"], "label": f["label"], "mode": "favourite", "spec": f["spec"]}
    raise CompareError(f"'{ref}' is not a cohort reference.")


def _side(svc, item):
    store = svc.store
    result = engine.run(item["spec"], store)
    v = svc._verdict(result)
    ids = set(result.cohort_ids)
    m = store.manifest.set_index("sample_id")
    patients = set(m.loc[m.index.intersection(list(ids)), "patient_id"])
    return {"meta": {"ref": item["ref"], "kind": item["kind"], "id": item["id"], "label": item["label"], "mode": item["mode"],
                     "n_samples": len(ids), "n_patients": len(patients), "verdict": v.verdict, "reason": v.reason,
                     "inflated": bool(result.unresolved_inflation), "ran": result.ok,
                     "checks": [{"name": c.name, "status": c.status} for c in v.checks],
                     "steps": [describe_step(st) for st in item["spec"]["steps"]]},
            "ids": ids, "patients": patients, "spec": item["spec"]}


def _composition(store, a_ids, b_ids):
    comp = store.composition_frame()
    out = []
    for col in CHARACTERISTICS:
        if col not in comp.columns:
            continue
        pa = comp.loc[comp.index.intersection(list(a_ids)), col].value_counts()
        pb = comp.loc[comp.index.intersection(list(b_ids)), col].value_counts()
        na, nb = int(pa.sum()), int(pb.sum())
        if not na or not nb:
            continue
        sa, sb = pa / na, pb / nb
        cats = sa.index.union(sb.index)
        tvd = float(0.5 * (sa.reindex(cats, fill_value=0) - sb.reindex(cats, fill_value=0)).abs().sum())
        ranked = sorted(cats, key=lambda c: -max(sa.get(c, 0), sb.get(c, 0)))
        keep, rest = ranked[:7], ranked[7:]
        rows = [{"group": str(c), "a_pct": round(100 * float(sa.get(c, 0)), 1), "b_pct": round(100 * float(sb.get(c, 0)), 1),
                 "a_n": int(pa.get(c, 0)), "b_n": int(pb.get(c, 0))} for c in keep]
        if rest:
            rows.append({"group": f"Other ({len(rest)} groups)", "a_pct": round(100 * float(sa.reindex(rest, fill_value=0).sum()), 1),
                         "b_pct": round(100 * float(sb.reindex(rest, fill_value=0).sum()), 1),
                         "a_n": int(pa.reindex(rest, fill_value=0).sum()), "b_n": int(pb.reindex(rest, fill_value=0).sum())})
        out.append({"name": col, "tvd": round(tvd, 3), "rows": rows})
    return sorted(out, key=lambda c: -c["tvd"])


def _coverage(store, a_ids, b_ids):
    m = store.manifest.set_index("sample_id")
    slides = store.slides
    dx = set(slides.loc[slides["slide_kind"] == "diagnostic (FFPE)", "sample_id"])
    out = []
    for key, label in LAYERS + [("dx_slide", "Diagnostic (FFPE) slide")]:
        row = {"layer": key, "label": label}
        for side, ids in (("a", a_ids), ("b", b_ids)):
            idx = m.index.intersection(list(ids))
            have = int(len(set(idx) & dx)) if key == "dx_slide" else int(m.loc[idx, LAYER_COL[key]].sum())
            row[f"{side}_n"] = have
            row[f"{side}_pct"] = round(100 * have / len(idx), 1) if len(idx) else 0.0
        out.append(row)
    return out


def _outcomes(store, a, b):
    if not store.available("survival"):
        return None
    surv = store.table("survival").drop_duplicates("sample_id").set_index("sample_id")
    m = store.manifest.set_index("sample_id")
    out = {}
    for key, (tcol, ecol), label in (("os", ("OS.time", "OS"), "Overall survival"),
                                     ("pfi", ("PFI.time", "PFI"), "Progression-free interval")):
        if tcol not in surv.columns:
            continue
        frames = {}
        for side, s in (("a", a), ("b", b)):
            idx = surv.index.intersection(list(s["ids"]))
            df = surv.loc[idx, [tcol, ecol]].copy()
            df["patient"] = m.loc[idx, "patient_id"].values
            frames[side] = df.drop_duplicates("patient")
        entry = {"label": label, "a": km_curve(frames["a"][tcol], frames["a"][ecol]),
                 "b": km_curve(frames["b"][tcol], frames["b"][ecol]), "logrank": None}
        if not (a["patients"] & b["patients"]):
            chi2, p = logrank(frames["a"][tcol], frames["a"][ecol], frames["b"][tcol], frames["b"][ecol])
            if p is not None:
                entry["logrank"] = {"chi2": chi2, "p": round(p, 4)}
        out[key] = entry
    return out


def _protocol(a_spec, b_spec):
    key = lambda st: json.dumps(st, sort_keys=True)  # noqa: E731
    a_keys = [key(st) for st in a_spec["steps"]]
    b_keys = [key(st) for st in b_spec["steps"]]
    rows = [{"label": describe_step(st), "in": "both" if k in b_keys else "a"} for st, k in zip(a_spec["steps"], a_keys)]
    rows += [{"label": describe_step(st), "in": "b"} for st, k in zip(b_spec["steps"], b_keys) if k not in a_keys]
    return {"rows": rows, "shared": sum(1 for r in rows if r["in"] == "both"),
            "only_a": sum(1 for r in rows if r["in"] == "a"), "only_b": sum(1 for r in rows if r["in"] == "b")}


def _summary(o, comp, cov, outcomes, proto, a, b):
    lines = []
    if o["shared"] == 0:
        lines.append(f"The cohorts share no samples: A has {a['n_samples']} and B has {b['n_samples']}.")
    else:
        lines.append(f"A has {a['n_samples']} samples and B has {b['n_samples']}; {o['shared']} are in both "
                     f"(Jaccard {o['jaccard']:.2f}), {o['only_a']} only in A and {o['only_b']} only in B.")
    for c in [c for c in comp if c["tvd"] >= 0.10][:3]:
        top = max(c["rows"], key=lambda r: abs(r["a_pct"] - r["b_pct"]))
        lines.append(f"{c['name']} differs most at {top['group']}: {top['a_pct']}% of A vs {top['b_pct']}% of B "
                     f"(TVD {c['tvd']:.2f}).")
    for r in cov:
        if abs(r["a_pct"] - r["b_pct"]) >= 10:
            lines.append(f"{r['label']}: {r['a_pct']}% of A vs {r['b_pct']}% of B.")
    if outcomes and outcomes.get("pfi") and outcomes["pfi"]["a"] and outcomes["pfi"]["b"]:
        pa, pb = outcomes["pfi"]["a"], outcomes["pfi"]["b"]
        lines.append(f"Progression events: {pa['events']} of {pa['n']} patients in A ({pa['event_pct']}%) vs "
                     f"{pb['events']} of {pb['n']} in B ({pb['event_pct']}%).")
    lines.append(f"Their protocols share {proto['shared']} step(s); A has {proto['only_a']} of its own and B has {proto['only_b']}.")
    if a["verdict"] != b["verdict"]:
        lines.append(f"Verdicts differ: A is {a['verdict']} and B is {b['verdict']}.")
    return lines


def compare(svc, user, ref_a, ref_b):
    if ref_a == ref_b:
        raise CompareError("Pick two different cohorts.")
    a = _side(svc, resolve(svc, user, ref_a))
    b = _side(svc, resolve(svc, user, ref_b))
    shared = a["ids"] & b["ids"]
    union = a["ids"] | b["ids"]
    overlap = {"shared": len(shared), "only_a": len(a["ids"] - b["ids"]), "only_b": len(b["ids"] - a["ids"]),
               "union": len(union), "jaccard": round(len(shared) / len(union), 4) if union else 0.0,
               "overlap_coefficient": round(len(shared) / min(len(a["ids"]), len(b["ids"])), 4) if a["ids"] and b["ids"] else 0.0,
               "shared_patients": len(a["patients"] & b["patients"])}
    comp = _composition(svc.store, a["ids"], b["ids"])
    cov = _coverage(svc.store, a["ids"], b["ids"])
    outcomes = _outcomes(svc.store, a, b)
    proto = _protocol(a["spec"], b["spec"])
    return {"a": a["meta"], "b": b["meta"], "overlap": overlap, "composition": comp, "coverage": cov,
            "outcomes": outcomes, "protocol": proto,
            "summary": _summary(overlap, comp, cov, outcomes, proto, a["meta"], b["meta"])}


def metrics_for_model(c):
    """The aggregate-only view the language model sees (no sample identifiers, no curves)."""
    side = lambda s: {k: s[k] for k in ("label", "n_samples", "n_patients", "verdict", "reason", "steps")}  # noqa: E731
    out = {"A": side(c["a"]), "B": side(c["b"]), "overlap": c["overlap"],
           "composition": [{"characteristic": x["name"], "tvd": x["tvd"], "groups": x["rows"]} for x in c["composition"]],
           "coverage": [{k: r[k] for k in ("label", "a_pct", "b_pct", "a_n", "b_n")} for r in c["coverage"]],
           "protocol": c["protocol"]}
    if c["outcomes"]:
        out["outcomes"] = {k: {"label": v["label"], "logrank": v["logrank"],
                               "A": {x: v["a"][x] for x in ("n", "events", "event_pct", "median_years", "median_followup_years")} if v["a"] else None,
                               "B": {x: v["b"][x] for x in ("n", "events", "event_pct", "median_years", "median_followup_years")} if v["b"] else None}
                           for k, v in c["outcomes"].items()}
    return out
