"""Calling a language model for a JSON draft: OpenAI-compatible endpoints (including local models) or Anthropic.

Cost and safety controls for a user-supplied key:
* output is capped (max tokens), JSON mode is requested where the provider supports it;
* an optional cache (any object with get(key) / set(key, text, usage)) means a repeated request costs nothing;
* reasoning models (gpt-5*, o1/o3/o4) get the parameters they accept (no temperature, max_completion_tokens);
* the key is read from the environment or passed in; it is never logged, cached or returned.
"""
import hashlib
import json
import os
import re
import urllib.error
import urllib.request

DEFAULT_MODELS = {"openai": "gpt-4.1-mini", "anthropic": "claude-haiku-5-5"}
REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")


class AIError(Exception):
    pass


def parse_json(text):
    """The first JSON object in a model reply (tolerates code fences or a sentence around it)."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise AIError("The model didn't return JSON.")
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise AIError(f"The model returned malformed JSON ({exc.msg}).") from None
    if not isinstance(data, dict):
        raise AIError("The model returned JSON that isn't an object.")
    return data


def _usage(raw, provider):
    u = (raw or {}).get("usage") or {}
    if provider == "anthropic":
        return {"input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0),
                "cached_input": u.get("cache_read_input_tokens", 0), "reasoning": 0}
    return {"input": u.get("prompt_tokens", 0), "output": u.get("completion_tokens", 0),
            "cached_input": (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0),
            "reasoning": (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)}


class LLMClient:
    def __init__(self, provider=None, model=None, api_key=None, base_url=None, cache=None, timeout=90,
                 max_output_tokens=None):
        env = os.environ
        self.provider = (provider or env.get("COHORT_STUDIO_PROVIDER")
                         or ("anthropic" if env.get("ANTHROPIC_API_KEY") and not env.get("OPENAI_API_KEY") else "openai"))
        if self.provider not in ("openai", "anthropic"):
            raise AIError(f"Unknown provider '{self.provider}'. Use 'openai' (any OpenAI-compatible endpoint) or 'anthropic'.")
        self.model = model or env.get("COHORT_STUDIO_MODEL") or DEFAULT_MODELS[self.provider]
        self.base_url = (base_url or env.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self._key = api_key or env.get("ANTHROPIC_API_KEY" if self.provider == "anthropic" else "OPENAI_API_KEY", "")
        self.cache = cache
        self.timeout = timeout
        self.max_output_tokens = int(max_output_tokens or env.get("COHORT_STUDIO_MAX_OUTPUT_TOKENS", 1800))
        self.reasoning_effort = env.get("COHORT_STUDIO_REASONING_EFFORT", "low")

    @property
    def is_local(self):
        return self.provider == "openai" and not self.base_url.startswith("https://api.openai.com")

    @property
    def configured(self):
        return bool(self._key) or self.is_local

    @property
    def label(self):
        return f"{self.provider}:{self.model}"

    def describe(self):
        where = "local endpoint" if self.is_local else self.provider
        return {"provider": self.provider, "model": self.model, "where": where, "configured": self.configured}

    def complete_json(self, system, user):
        """Return (data, usage, cached)."""
        key = hashlib.sha256(json.dumps([self.provider, self.model, self.base_url if self.is_local else "",
                                         system, user]).encode()).hexdigest()
        if self.cache is not None:
            hit = self.cache.get(key)
            if hit is not None:
                text, usage = hit
                return parse_json(text), usage or {}, True
        if not self.configured:
            raise AIError("No API key. Put OPENAI_API_KEY (or ANTHROPIC_API_KEY) in the environment or .env, "
                          "or point OPENAI_BASE_URL at a local model.")
        text, usage = self._request(system, user)
        data = parse_json(text)
        if self.cache is not None:
            self.cache.set(key, text, usage)
        return data, usage, False

    # ---------- providers ----------
    def _post(self, url, payload, headers):
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"content-type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            try:
                msg = json.loads(body).get("error", {})
                msg = msg.get("message", body) if isinstance(msg, dict) else str(msg)
            except (json.JSONDecodeError, AttributeError):
                msg = body
            raise AIError(f"The model request failed (HTTP {exc.code}): {msg[:300]}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise AIError(f"Couldn't reach the model ({getattr(exc, 'reason', exc)}).") from None

    def _request(self, system, user):
        if self.provider == "anthropic":
            raw = self._post("https://api.anthropic.com/v1/messages",
                             {"model": self.model, "max_tokens": self.max_output_tokens, "temperature": 0,
                              "system": system, "messages": [{"role": "user", "content": user}]},
                             {"x-api-key": self._key, "anthropic-version": "2023-06-01"})
            text = "".join(b.get("text", "") for b in raw.get("content", []))
            return text, _usage(raw, "anthropic")
        payload = {"model": self.model, "response_format": {"type": "json_object"},
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if self.model.lower().startswith(REASONING_PREFIXES):
            # Reasoning models reject temperature, and their hidden reasoning counts against the output cap.
            payload["max_completion_tokens"] = self.max_output_tokens * 3
            if self.reasoning_effort:
                payload["reasoning_effort"] = self.reasoning_effort
        else:
            payload["temperature"] = 0
            payload["max_tokens"] = self.max_output_tokens
        headers = {"authorization": f"Bearer {self._key}"} if self._key else {}
        raw = self._post(f"{self.base_url}/chat/completions", payload, headers)
        choice = (raw.get("choices") or [{}])[0]
        if choice.get("finish_reason") == "length":
            raise AIError("The model ran out of output tokens before finishing. Raise COHORT_STUDIO_MAX_OUTPUT_TOKENS.")
        return (choice.get("message") or {}).get("content") or "", _usage(raw, "openai")


class ScriptedClient:
    """A stand-in model for tests and offline demos: returns a fixed reply per request text."""

    def __init__(self, replies, default=None):
        self.replies, self.default = replies, default
        self.provider, self.model, self.calls = "scripted", "scripted", 0
        self.configured, self.is_local, self.cache = True, True, None

    @property
    def label(self):
        return "scripted"

    def describe(self):
        return {"provider": "scripted", "model": "scripted", "where": "offline", "configured": True}

    def complete_json(self, system, user):
        self.calls += 1
        request = user.split("\n", 1)[0].replace("REQUEST:", "").strip()
        reply = self.replies.get(request, self.default)
        if reply is None:
            raise AIError(f"No scripted reply for '{request}'.")
        data = reply if isinstance(reply, dict) else parse_json(reply)
        return json.loads(json.dumps(data)), {"input": 0, "output": 0, "cached_input": 0, "reasoning": 0}, False
