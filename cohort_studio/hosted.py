"""The hosted Cohort Studio AI service: the AI route for anyone without their own model key.

The service (frontend/server.py, deployed by the team) holds the model key. It receives only the request text, runs
the same grounded AI route on the same frozen release, and returns the drafted steps; this computer then runs them on
its own copy of the data. No data files, cohorts or keys are sent.

    COHORT_STUDIO_API_URL      the service (default: the team's deployment below)
    COHORT_STUDIO_API_TOKEN    optional personal token for a higher daily limit (from the team; never commit it)
    COHORT_STUDIO_HOSTED=off   never call the service: your own key or the built-in parser only
"""
import json
import os
import urllib.error
import urllib.request

DEFAULT_URL = "https://oncosys-cohort-studio.onrender.com"
MAX_REQUEST_CHARS = 2000


class HostedUnavailable(Exception):
    pass


def enabled():
    return os.environ.get("COHORT_STUDIO_HOSTED", "on").strip().lower() not in ("off", "0", "false", "no")


def base_url():
    return (os.environ.get("COHORT_STUDIO_API_URL") or DEFAULT_URL).rstrip("/")


def _call(method, path, payload=None, timeout=None):
    headers = {"content-type": "application/json", "user-agent": "cohort-studio-client"}
    token = os.environ.get("COHORT_STUDIO_API_TOKEN", "").strip()
    if token:
        headers["authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{base_url()}{path}", method=method, headers=headers,
                                 data=json.dumps(payload).encode() if payload is not None else None)
    timeout = timeout or float(os.environ.get("COHORT_STUDIO_HOSTED_TIMEOUT", 150))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            msg = json.loads(exc.read().decode()).get("error") or ""
        except (ValueError, AttributeError):
            msg = ""
        if exc.code == 401:
            msg = msg or "it needs a valid COHORT_STUDIO_API_TOKEN"
        raise HostedUnavailable(f"the hosted AI service declined (HTTP {exc.code}): {msg}"[:400]) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise HostedUnavailable(f"the hosted AI service can't be reached ({getattr(exc, 'reason', exc)})") from None
    except ValueError:
        raise HostedUnavailable("the hosted AI service sent a reply that isn't JSON") from None


def draft(request):
    """Ask the service to draft a request. Returns its reply: {"spec", "problems", "model", "limits"}."""
    if not enabled():
        raise HostedUnavailable("the hosted AI service is switched off (COHORT_STUDIO_HOSTED=off)")
    reply = _call("POST", "/api/v1/draft", {"request": str(request)[:MAX_REQUEST_CHARS]})
    if not isinstance(reply, dict) or not isinstance(reply.get("spec"), dict) \
            or not isinstance(reply["spec"].get("steps"), list):
        raise HostedUnavailable("the hosted AI service sent a reply without steps")
    return reply


def status():
    """Whether the service has a model, and what's left of your limits today."""
    return _call("GET", "/api/v1/status", timeout=float(os.environ.get("COHORT_STUDIO_HOSTED_TIMEOUT", 150)))
