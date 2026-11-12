"""Optional AI step: turn a request into a spec with a language model.

The model only drafts the editable steps. It never sees patient-level data and never produces
numbers: every count comes from the cohort engine running on the frozen files. Keys are read
from the environment or typed into the app by the user; nothing is stored in the repository.

Providers
  anthropic : ANTHROPIC_API_KEY, model from COHORT_STUDIO_MODEL (default below)
  openai    : any OpenAI-compatible endpoint, including a local model, e.g.
              OPENAI_BASE_URL=http://localhost:11434/v1 COHORT_STUDIO_MODEL=llama3.1  (Ollama, no key needed)
"""
import json
import os
import re
import urllib.request

from . import ops
from .data import CLINICAL_FIELDS, SURVIVAL_FIELDS
from .spec import new_spec, validate

DEFAULT_ANTHROPIC_MODEL = "claude-haiku-5-5"

SYSTEM = """You turn a researcher's plain-English request for a TCGA breast cancer cohort into JSON steps.
Return ONLY a JSON object: {"steps": [...], "unparsed": [words you could not map], "notes": [assumptions you made]}.
Do not invent fields, values or numbers. If part of the request can't be expressed, put it in "unparsed".

Step types (run in order, starting from every sample in the release):
{"op":"sample_type","values":[one or more of %(types)s]}   (always first; use "Primary Tumor" for "tumours")
{"op":"layer","layer": one of %(layers)s}                     (sample must have that data)
{"op":"mutation","gene":"TP53","mutated":true}               (gene-level mutation call; needs layer mutation first)
{"op":"join","table":"clinical","key":"sample_id","mode":"rows"}   (needed before any clinical filter)
{"op":"join","table":"survival","key":"sample_id","mode":"rows"}   (needed before any survival filter)
{"op":"join","table":"slides","key":"sample_id","mode":"rows","slide_kinds":[subset of %(kinds)s, or [] for any]}
{"op":"join","table":"normals","key":"patient_id","mode":"rows","add_to_cohort":false}  (patient also has a normal-tissue sample; add_to_cohort true for tumour-normal pairs)
{"op":"filter","table":"clinical","field": one of %(clin)s, "cmp": one of ["in","not_in","notnull","gte","lte","between","stage_in","contains"], "value": ...}
{"op":"filter","table":"survival","field": one of %(surv)s, "cmp": "notnull" or "is_event", "value": null}

Values: er_status/pr_status/her2_status use "Positive"/"Negative"; pam50 uses "LumA","LumB","Her2","Basal","Normal";
stage_in uses ["I","II","III","IV"]; age uses numbers (between takes [low, high]); gender uses "FEMALE"/"MALE";
histology uses "Infiltrating Ductal Carcinoma"/"Infiltrating Lobular Carcinoma"; menopause uses contains ["post"] or ["pre"].
Write joins with "mode":"rows" and key "sample_id" unless the user asks to join by patient. The app checks every join for
inflated counts and lets the user fix it, so do not try to hide duplicates yourself.
""" % {"types": json.dumps(ops.SAMPLE_TYPES), "layers": json.dumps(list(ops.LAYER_COLUMNS)),
       "kinds": json.dumps(ops.SLIDE_KINDS), "clin": json.dumps(list(CLINICAL_FIELDS)),
       "surv": json.dumps(list(SURVIVAL_FIELDS))}


class AIUnavailable(Exception):
    pass


def _post(url, payload, headers, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"content-type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _call(request, provider, api_key, model, base_url):
    if provider == "anthropic":
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise AIUnavailable("No Anthropic API key. Paste your own key in the sidebar or set ANTHROPIC_API_KEY.")
        out = _post("https://api.anthropic.com/v1/messages",
                    {"model": model or os.environ.get("COHORT_STUDIO_MODEL", DEFAULT_ANTHROPIC_MODEL),
                     "max_tokens": 1500, "system": SYSTEM, "messages": [{"role": "user", "content": request}]},
                    {"x-api-key": key, "anthropic-version": "2023-06-01"})
        return "".join(block.get("text", "") for block in out.get("content", []))
    base = (base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    key = api_key or os.environ.get("OPENAI_API_KEY", "")
    model = model or os.environ.get("COHORT_STUDIO_MODEL")
    if not model:
        raise AIUnavailable("Set a model name (COHORT_STUDIO_MODEL), for example a local Ollama model.")
    headers = {"authorization": f"Bearer {key}"} if key else {}
    out = _post(f"{base}/chat/completions",
                {"model": model, "temperature": 0,
                 "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": request}]}, headers)
    return out["choices"][0]["message"]["content"]


def ai_parse(request, store, provider="anthropic", api_key=None, model=None, base_url=None):
    """Return (spec, problems). Raises AIUnavailable if no model can be reached."""
    try:
        text = _call(request, provider, api_key, model, base_url)
    except AIUnavailable:
        raise
    except Exception as exc:
        raise AIUnavailable(f"The AI request failed ({exc}). The built-in parser still works without it.")
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise AIUnavailable("The AI didn't return JSON. The built-in parser still works without it.")
    data = json.loads(match.group(0))
    spec = new_spec(request, data.get("steps", []), parser=f"ai:{provider}")
    spec["unparsed"] = [str(w) for w in data.get("unparsed", [])]
    spec["notes"] = ["Steps drafted by an AI model: check each one before running."] + [str(n) for n in data.get("notes", [])]
    return spec, validate(spec, store)
