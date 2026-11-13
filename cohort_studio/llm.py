"""The AI route for the command line, the Streamlit app and Python: draft a spec from a plain-English request.

Which model drafts the steps, in order (draft_spec):
  1. your own model: OPENAI_API_KEY or ANTHROPIC_API_KEY (environment or the repository's git-ignored .env), or a
     local OpenAI-compatible model via OPENAI_BASE_URL (e.g. Ollama, no key needed);
  2. otherwise the hosted Cohort Studio AI service (cohort_studio/hosted.py), which holds the team's key and has
     daily limits; it receives only the request text;
  3. otherwise, or if the AI can't help (offline, limit reached, a draft that can't run), the built-in parser.
Whichever is used is written in the spec's notes, so the exported cohort says how it was drafted.

The work is done by cohort_studio.assistant: the model is given the release's real fields, values and counts, every
step it writes is checked against the data, and the draft is compared with the built-in parser's (reconcile.py).
The CLI and the Streamlit app can't ask back, so they use each question's recommended answer, list the question in
the notes and leave it unconfirmed (the verdict then asks a person to confirm it). The web app asks the user.

The model only drafts the editable steps. It never sees patient-level data and never produces numbers: every
count comes from the cohort engine running on the frozen files. No key is stored in the repository.

    COHORT_STUDIO_PROVIDER   openai | anthropic (default: whichever key is set)
    COHORT_STUDIO_MODEL      default gpt-4.1-mini / claude-haiku-5-5
"""
from . import hosted
from .env import load_dotenv
from .parse import parse_request
from .spec import validate

DEFAULT_ANTHROPIC_MODEL = "claude-haiku-5-5"


class AIUnavailable(Exception):
    pass


def spec_from_draft(draft):
    """The runnable spec for a draft with every question answered by its recommended option (and noted)."""
    from .assistant import to_spec
    spec = to_spec(draft)
    for q in draft["clarifications"]:
        options = "; ".join(f"{o['label']} ({o.get('n_samples', '?')} samples)" for o in q["options"])
        spec["notes"].append(f"The AI has a question: {q['question']} Options: {options}. "
                             "The first was used; edit the steps if you meant another.")
    return spec


def own_model(provider=None, api_key=None, model=None, base_url=None):
    """An LLMClient for the user's own key or local model, or None if none is set up."""
    from .assistant import AIError, LLMClient
    load_dotenv()
    try:
        client = LLMClient(provider=provider, model=model, api_key=api_key, base_url=base_url)
    except AIError:
        return None
    return client if client.configured else None


def ai_parse(request, store, provider=None, api_key=None, model=None, base_url=None, client=None):
    """Return (spec, problems) from your own model. Raises AIUnavailable if it can't be reached or its reply can't
    be used."""
    from .assistant import AIError, Catalog, LLMClient, make_draft
    try:
        client = client or LLMClient(provider=provider, model=model, api_key=api_key, base_url=base_url)
        draft = make_draft(request, store, client, catalog=Catalog.for_store(store), compare_with_rules=True)
    except AIError as exc:
        raise AIUnavailable(f"{exc} The built-in parser still works without it.") from None
    spec = spec_from_draft(draft)
    return spec, validate(spec, store)


def draft_spec(request, store, ai="auto", provider=None, api_key=None, model=None, base_url=None, client=None,
               log=None):
    """Draft a spec the best available way. ai: "auto" (own model -> hosted service -> built-in parser),
    "own" (own model -> built-in parser; nothing is sent to the hosted service) or "off" (built-in parser only).
    Returns (spec, route): route = {"used": "own" | "hosted" | "rules", "label": ..., "tried": [why others weren't used]}."""
    say = log or (lambda message: None)
    tried = []
    if ai in ("auto", "own"):
        own = client or own_model(provider, api_key, model, base_url)
        if own is not None:
            say(f"Drafting with your model ({own.label})...")
            try:
                spec, problems = ai_parse(request, store, client=own)
                if not problems:
                    return spec, {"used": "own", "label": f"AI, your model ({own.label})", "tried": tried}
                tried.append("your model's draft can't run: " + "; ".join(m for _, m in problems))
            except AIUnavailable as exc:
                tried.append(f"your model: {exc}")
        else:
            tried.append("no model key of your own (OPENAI_API_KEY, ANTHROPIC_API_KEY or OPENAI_BASE_URL)")
        if ai == "auto":
            if hosted.enabled():
                say("Asking the hosted Cohort Studio AI service...")
            try:
                reply = hosted.draft(request)
                spec, problems = reply["spec"], validate(reply["spec"], store)
                if not problems:
                    label = f"AI, hosted Cohort Studio service ({reply.get('model') or 'model'})"
                    spec.setdefault("notes", []).insert(0, f"Drafted by the hosted Cohort Studio AI service "
                                                           f"({reply.get('model') or 'model'}).")
                    return spec, {"used": "hosted", "label": label, "tried": tried, "limits": reply.get("limits")}
                tried.append("the hosted service's draft can't run here: " + "; ".join(m for _, m in problems))
            except hosted.HostedUnavailable as exc:
                tried.append(str(exc))
    spec = parse_request(request, store)
    if ai == "off":
        return spec, {"used": "rules", "label": "built-in parser (AI switched off)", "tried": []}
    spec["notes"].append("No AI draft was used (" + "; ".join(tried) + "), so the built-in parser drafted the steps.")
    return spec, {"used": "rules", "label": "built-in parser", "tried": tried}
