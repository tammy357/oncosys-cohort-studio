"""Optional AI step for the Streamlit app and the command line: draft a spec with a language model.

The work is done by cohort_studio.assistant: the model is given the release's real fields, values and counts,
every step it writes is checked against the data, and when a choice would change the cohort it asks a question.
The Streamlit app and the CLI can't ask back, so they use each question's recommended answer, list the question
in the notes and leave it unconfirmed (the verdict then asks a person to confirm it). The web app in frontend/
asks the user instead.

The model only drafts the editable steps. It never sees patient-level data and never produces numbers: every
count comes from the cohort engine running on the frozen files. Keys come from the environment or are typed
into the app by the user; nothing is stored in the repository.

Providers
  openai    : OPENAI_API_KEY; any OpenAI-compatible endpoint, including a local model, e.g.
              OPENAI_BASE_URL=http://localhost:11434/v1 COHORT_STUDIO_MODEL=llama3.1  (Ollama, no key needed)
  anthropic : ANTHROPIC_API_KEY
  COHORT_STUDIO_MODEL picks the model (defaults: gpt-4.1-mini, claude-haiku-5-5).
"""
from .spec import validate

DEFAULT_ANTHROPIC_MODEL = "claude-haiku-5-5"


class AIUnavailable(Exception):
    pass


def ai_parse(request, store, provider="anthropic", api_key=None, model=None, base_url=None):
    """Return (spec, problems). Raises AIUnavailable if no model can be reached or its reply can't be used."""
    from .assistant import AIError, Catalog, LLMClient, make_draft, to_spec
    try:
        client = LLMClient(provider=provider, model=model, api_key=api_key, base_url=base_url)
        draft = make_draft(request, store, client, catalog=Catalog.for_store(store))
    except AIError as exc:
        raise AIUnavailable(f"{exc} The built-in parser still works without it.") from None
    spec = to_spec(draft)
    for q in draft["clarifications"]:
        options = "; ".join(f"{o['label']} ({o.get('n_samples', '?')} samples)" for o in q["options"])
        spec["notes"].append(f"The AI has a question: {q['question']} Options: {options}. "
                             "The first was used; edit the steps if you meant another.")
    return spec, validate(spec, store)
