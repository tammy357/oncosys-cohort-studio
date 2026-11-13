"""Who may call the paid model, and how often: per-caller, per-address and per-minute limits plus a daily budget.

Every AI request (web drafts, comparison insights and the /api/v1 service the CLI uses) goes through UsageGuard:
* a caller is a personal-token holder (tokens come from COHORT_STUDIO_API_TOKENS in the server's environment and
  are never written to a file) or, without a token, a network address plus the browser's user id;
* each caller has a daily number of AI requests (more with a token) and at most a few per minute; an address
  without a token also has its own daily ceiling, shared by everyone behind it;
* the model's spend is estimated from the tokens each call used, and once today's estimate reaches the daily budget
  every caller gets the built-in parser until 00:00 UTC.
Counts live in the database, so a restart doesn't reset them. The budget is the hard stop; the per-caller limits
keep one honest caller from using it all. They are not proof against abuse: an anonymous caller's address comes from
X-Forwarded-For, which the server's proxy (Caddy) extends rather than replaces, so a client calling the server directly
can fake it (and the
user id). Tokens plus COHORT_STUDIO_REQUIRE_TOKEN close /api/v1 and /mcp; a model-provider spending limit is still
worth setting as a second guard.
"""
import collections
import dataclasses
import datetime
import threading
import time

# USD per million tokens: (input, cached input, output). Unknown models are priced high, so the budget errs safe.
PRICES = {"gpt-4.1-nano": (0.10, 0.025, 0.40), "gpt-4.1-mini": (0.40, 0.10, 1.60), "gpt-4.1": (2.00, 0.50, 8.00),
          "gpt-4o-mini": (0.15, 0.075, 0.60), "gpt-4o": (2.50, 1.25, 10.00)}
UNKNOWN_PRICE = (3.00, 3.00, 15.00)


def estimate_cost(model, usage):
    usage = usage or {}
    name = str(model or "").lower()
    price = next((p for m, p in sorted(PRICES.items(), key=lambda kv: -len(kv[0])) if name.startswith(m)), UNKNOWN_PRICE)
    cached = int(usage.get("cached_input", 0) or 0)
    fresh = max(0, int(usage.get("input", 0) or 0) - cached)
    return (fresh * price[0] + cached * price[1] + int(usage.get("output", 0) or 0) * price[2]) / 1_000_000


@dataclasses.dataclass(frozen=True)
class Caller:
    identity: str                   # token:<name> | anon:<address>|<user>
    ip: str
    token_name: str = None

    @property
    def tier(self):
        return "token" if self.token_name else "anonymous"


class LimitExceeded(Exception):
    pass


class UsageGuard:
    def __init__(self, settings, storage, clock=time.time):
        self.s, self.db, self.clock = settings, storage, clock
        self._recent = collections.defaultdict(collections.deque)
        self._lock = threading.Lock()

    def _day(self):
        return datetime.datetime.fromtimestamp(self.clock(), datetime.timezone.utc).strftime("%Y-%m-%d")

    def _daily(self, caller):
        return self.s.ai_token_daily if caller.token_name else self.s.ai_user_daily

    def check(self, caller):
        """Raise LimitExceeded if this caller may not use the model now (counts the attempt for the minute window)."""
        day = self._day()
        if self.db.usage(day, "global")["cost"] >= self.s.ai_daily_budget_usd:
            raise LimitExceeded("Today's AI budget for this service is used up, so the built-in parser drafts the steps "
                                "until 00:00 UTC. Your own API key still works.")
        if self.db.usage(day, f"caller:{caller.identity}")["calls"] >= self._daily(caller):
            raise LimitExceeded(f"You've used today's {self._daily(caller)} AI requests on this service, so the built-in "
                                "parser drafts the steps until 00:00 UTC. Your own API key still works.")
        if not caller.token_name and self.db.usage(day, f"ip:{caller.ip}")["calls"] >= self.s.ai_ip_daily:
            raise LimitExceeded("Your network has used today's AI requests on this service, so the built-in parser "
                                "drafts the steps until 00:00 UTC. A personal token or your own API key still works.")
        now = self.clock()
        with self._lock:
            recent = self._recent[caller.identity]
            while recent and now - recent[0] > 60:
                recent.popleft()
            if len(recent) >= self.s.ai_per_minute:
                raise LimitExceeded(f"At most {self.s.ai_per_minute} AI requests a minute; wait a moment and try again.")
            recent.append(now)

    def record(self, caller, usage, model, cached=False):
        cost = 0.0 if cached else estimate_cost(model, usage)
        day = self._day()
        keys = ["global", f"caller:{caller.identity}"] + ([] if caller.token_name else [f"ip:{caller.ip}"])
        for key in keys:
            self.db.add_usage(day, key, 1, cost)
        return cost

    def status(self, caller):
        day = self._day()
        used = self.db.usage(day, f"caller:{caller.identity}")["calls"]
        spent = self.db.usage(day, "global")["cost"]
        return {"tier": caller.tier, "requests_today": used, "requests_per_day": self._daily(caller),
                "requests_left_today": max(0, self._daily(caller) - used), "per_minute": self.s.ai_per_minute,
                "service_budget_left_today": spent < self.s.ai_daily_budget_usd, "resets": "00:00 UTC"}


class RequestRate:
    """Any /api request, per network address per minute (in memory): basic protection for the free server."""

    def __init__(self, per_minute, clock=time.monotonic):
        self.per_minute, self.clock = per_minute, clock
        self._hits = collections.defaultdict(collections.deque)
        self._lock = threading.Lock()

    def allow(self, key):
        now = self.clock()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return False
            hits.append(now)
            if len(self._hits) > 10_000:               # forget idle addresses so memory stays bounded
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > 60][:5_000]:
                    del self._hits[k]
            return True
