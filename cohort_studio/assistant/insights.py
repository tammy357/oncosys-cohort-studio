"""AI insights on two cohorts, grounded in metrics the engine computed.

The model sees only aggregate numbers (sizes, overlap, composition shares, data coverage, outcome summaries,
protocol steps, verdicts), never sample identifiers or patient-level rows. Every number in its reply is checked
against those metrics; any number that isn't there (or isn't a simple difference of two of them) is reported, so
a person can check it before trusting the text.
"""
import json
import re

RULES = """You compare two research cohorts (A and B) built from the same frozen TCGA-BRCA release, for a cancer researcher deciding which cohort fits which analysis. You receive only METRICS computed by the cohort engine.

Rules
1. Use only numbers that appear in METRICS, or the difference between two of them. Never invent or estimate a number.
2. Ground every statement in METRICS. If something can't be judged from them, say so.
3. Research use only: no diagnosis, treatment advice, risk scores or claims of clinical validity. Kaplan-Meier and log-rank numbers are descriptive and unadjusted.
4. Be specific and brief. Name the characteristic and the group (for example "Basal-like is 31% of A vs 4% of B").
5. Overlapping cohorts share samples: mention it when it matters for comparing them.

Return only this JSON object:
{"headline": "one sentence on the most important difference",
 "differences": [{"title": "...", "detail": "..."}],      3 to 5 items, most important first
 "similarities": [{"title": "...", "detail": "..."}],     2 to 4 items
 "applications": {"a": "analyses A suits better, and why", "b": "analyses B suits better, and why"},
 "cautions": ["bias, size or data-quality points a reviewer should check"]}"""

_NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\w])")


def _walk_numbers(obj, out):
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _walk_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _walk_numbers(v, out)
    elif isinstance(obj, str):
        for m in _NUM.finditer(obj):
            out.add(float(m.group(1).replace(",", "")))


def _pairs(obj, out):
    """Differences of paired A/B values, which the model may legitimately quote."""
    if isinstance(obj, dict):
        for a_key in [k for k in obj if k.startswith("a_") or k == "A"]:
            b_key = "b_" + a_key[2:] if a_key.startswith("a_") else "B"
            if b_key in obj and isinstance(obj[a_key], (int, float)) and isinstance(obj[b_key], (int, float)):
                out.add(abs(float(obj[a_key]) - float(obj[b_key])))
        if isinstance(obj.get("A"), dict) and isinstance(obj.get("B"), dict):
            for k, v in obj["A"].items():
                w = obj["B"].get(k)
                if isinstance(v, (int, float)) and isinstance(w, (int, float)) and not isinstance(v, bool):
                    out.add(abs(float(v) - float(w)))
        for v in obj.values():
            _pairs(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _pairs(v, out)


def allowed_numbers(metrics):
    nums = set()
    _walk_numbers(metrics, nums)
    _pairs(metrics, nums)
    extra = set()
    for n in nums:
        extra |= {round(n), round(n, 1), round(n, 2)}
        if 0 <= n <= 1:
            extra |= {round(100 * n), round(100 * n, 1)}       # fractions quoted as percentages
    return nums | extra


def unverified_numbers(text, metrics):
    allowed = allowed_numbers(metrics)
    found = []
    for m in _NUM.finditer(text):
        raw = m.group(1)
        v = float(raw.replace(",", ""))
        if v < 10 and "." not in raw:          # small counts like "two steps" or "3 groups"
            continue
        if 1990 <= v <= 2030 and "." not in raw:  # years
            continue
        if not any(abs(v - a) <= 0.11 or (a and abs(v - a) / abs(a) <= 0.005) for a in allowed):
            found.append(raw)
    return list(dict.fromkeys(found))


def _items(value):
    out = []
    for x in value or []:
        if isinstance(x, dict) and (x.get("title") or x.get("detail")):
            out.append({"title": str(x.get("title") or ""), "detail": str(x.get("detail") or "")})
        elif isinstance(x, str) and x.strip():
            out.append({"title": "", "detail": x.strip()})
    return out


def generate(client, metrics):
    """Ask the model for insights. Returns the insights plus usage and any unverified numbers. Raises AIError."""
    data, usage, cached = client.complete_json(RULES, "METRICS: " + json.dumps(metrics, separators=(",", ":")))
    apps = data.get("applications") if isinstance(data.get("applications"), dict) else {}
    out = {"headline": str(data.get("headline") or ""),
           "differences": _items(data.get("differences"))[:6],
           "similarities": _items(data.get("similarities"))[:5],
           "applications": {"a": str(apps.get("a") or ""), "b": str(apps.get("b") or "")},
           "cautions": [str(c.get("detail") if isinstance(c, dict) else c) for c in (data.get("cautions") or []) if c][:6],
           "usage": usage, "cached": cached, "model": getattr(client, "model", "")}
    text = " ".join([out["headline"], out["applications"]["a"], out["applications"]["b"], *out["cautions"],
                     *[f"{x['title']} {x['detail']}" for x in out["differences"] + out["similarities"]]])
    out["unverified_numbers"] = unverified_numbers(text, metrics)
    return out
