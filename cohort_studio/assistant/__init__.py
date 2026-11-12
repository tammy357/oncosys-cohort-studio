"""The grounded AI route: draft a cohort from plain English using the release's real fields and values,
ask the user when a choice would change the cohort, and never guess silently.

The built-in rule parser (cohort_studio/parse.py) is separate and unchanged.
"""
from .catalog import Catalog
from .client import AIError, LLMClient, ScriptedClient
from .draft import assemble, draft_from_spec, make_draft, materialize, review_items, run_cohort, to_spec

__all__ = ["Catalog", "AIError", "LLMClient", "ScriptedClient", "assemble", "draft_from_spec", "make_draft",
           "materialize", "review_items", "run_cohort", "to_spec"]
