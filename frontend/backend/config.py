"""Settings for the web app. Everything comes from the environment or the repository's .env (git-ignored).

AI limits (see limits.py); every value can be changed with the variable named next to it:
    COHORT_STUDIO_DAILY_BUDGET_USD   estimated model spend per UTC day for the whole service (default 4)
    COHORT_STUDIO_USER_DAILY_LIMIT   AI requests per caller per day without a token (default 100)
    COHORT_STUDIO_TOKEN_DAILY_LIMIT  AI requests per token holder per day (default 500)
    COHORT_STUDIO_IP_DAILY_LIMIT     AI requests per network address per day without a token (default 400)
    COHORT_STUDIO_PER_MINUTE_LIMIT   AI requests per caller per minute (default 10)
    COHORT_STUDIO_API_TOKENS         personal tokens, "name:token,name:token" (environment only; never in a file
                                     that is committed)
    COHORT_STUDIO_REQUIRE_TOKEN      on = /api/v1 and /mcp only answer token holders; the web app stays open (default off)
    COHORT_STUDIO_REQUESTS_PER_MINUTE  any /api request, per network address (default 240)
"""
import os
import pathlib

from cohort_studio.env import load_dotenv

FRONTEND = pathlib.Path(__file__).resolve().parent.parent
REPO = FRONTEND.parent


def _on(value):
    return str(value).strip().lower() not in ("off", "0", "false", "no", "")


def parse_tokens(text):
    """{token: name} from "name:token,name:token" (a bare token is named token-1, token-2, ...)."""
    out = {}
    for i, item in enumerate([t.strip() for t in str(text or "").replace("\n", ",").split(",") if t.strip()], 1):
        name, _, token = item.rpartition(":")
        if len(token.strip()) >= 16:                   # short tokens are refused: they're guessable
            out[token.strip()] = (name.strip() or f"token-{i}")[:40]
    return out


class Settings:
    def __init__(self, **overrides):
        self.dotenv_loaded = load_dotenv(REPO / ".env")
        env = os.environ

        def get(name, var, default, cast):
            value = overrides.get(name)
            return cast(value if value is not None else env.get(var, default))

        self.data_dir = overrides.get("data_dir") or env.get("COHORT_STUDIO_DATA_DIR") or str(REPO / "data" / "tcga-brca")
        default_db = (FRONTEND / ".data" / "cohort_studio.db").as_posix()
        self.database_url = overrides.get("database_url") or env.get("DATABASE_URL") or f"sqlite:///{default_db}"
        self.ai_enabled = overrides.get("ai_enabled", _on(env.get("COHORT_STUDIO_AI", "on")))
        # A ceiling on paid model calls per server run, so a low-balance key can't be drained by accident.
        self.ai_call_limit = int(overrides.get("ai_call_limit") or env.get("COHORT_STUDIO_AI_CALL_LIMIT", 200))
        self.ai_daily_budget_usd = get("ai_daily_budget_usd", "COHORT_STUDIO_DAILY_BUDGET_USD", 4.0, float)
        self.ai_user_daily = get("ai_user_daily", "COHORT_STUDIO_USER_DAILY_LIMIT", 100, int)
        self.ai_token_daily = get("ai_token_daily", "COHORT_STUDIO_TOKEN_DAILY_LIMIT", 500, int)
        self.ai_ip_daily = get("ai_ip_daily", "COHORT_STUDIO_IP_DAILY_LIMIT", 400, int)
        self.ai_per_minute = get("ai_per_minute", "COHORT_STUDIO_PER_MINUTE_LIMIT", 10, int)
        self.requests_per_minute = get("requests_per_minute", "COHORT_STUDIO_REQUESTS_PER_MINUTE", 240, int)
        self.require_token = get("require_token", "COHORT_STUDIO_REQUIRE_TOKEN", "off", _on)
        tokens = overrides.get("api_tokens")
        self.api_tokens = tokens if isinstance(tokens, dict) else parse_tokens(env.get("COHORT_STUDIO_API_TOKENS", ""))
        self.max_body_bytes = get("max_body_bytes", "COHORT_STUDIO_MAX_BODY_BYTES", 1_000_000, int)

    def __repr__(self):                                # never print the tokens
        return f"Settings(data_dir={self.data_dir!r}, ai_enabled={self.ai_enabled}, tokens={len(self.api_tokens)})"
