"""Searches, favourites, drafts and the model-response cache.

SQLite locally (frontend/.data/cohort_studio.db); set DATABASE_URL to use Postgres instead, e.g.
    DATABASE_URL=postgresql+psycopg://cohort:cohort@localhost:5432/cohort_studio
(requires `pip install "psycopg[binary]"`). The same code runs on both through SQLAlchemy Core.
"""
import datetime
import json
import pathlib
import uuid

from sqlalchemy import (Column, DateTime, Float, Integer, MetaData, String, Table, Text, create_engine, delete, insert,
                        select, update)
from sqlalchemy.exc import IntegrityError

meta = MetaData()

users = Table("users", meta,
              Column("id", String(64), primary_key=True),
              Column("created_at", DateTime, nullable=False))

searches = Table("searches", meta,
                 Column("id", Integer, primary_key=True, autoincrement=True),
                 Column("user_id", String(64), index=True, nullable=False),
                 Column("created_at", DateTime, nullable=False),
                 Column("updated_at", DateTime, nullable=False),
                 Column("query", Text, nullable=False),
                 Column("mode", String(16), nullable=False),            # ai | rules | advanced
                 Column("spec_json", Text, nullable=False),
                 Column("draft_id", String(36)),
                 Column("n_samples", Integer),
                 Column("n_patients", Integer),
                 Column("verdict", String(32)),
                 Column("reason", Text))

favorites = Table("favorites", meta,
                  Column("id", Integer, primary_key=True, autoincrement=True),
                  Column("user_id", String(64), index=True, nullable=False),
                  Column("created_at", DateTime, nullable=False),
                  Column("label", Text, nullable=False),
                  Column("query", Text, nullable=False),
                  Column("spec_json", Text),                             # set when saved from a run: reruns skip the model
                  Column("search_id", Integer))

drafts = Table("drafts", meta,
               Column("id", String(36), primary_key=True),
               Column("user_id", String(64), index=True, nullable=False),
               Column("created_at", DateTime, nullable=False),
               Column("query", Text, nullable=False),
               Column("draft_json", Text, nullable=False))

versions = Table("versions", meta,                               # every saved state of a cohort, oldest first
                 Column("id", Integer, primary_key=True, autoincrement=True),
                 Column("search_id", Integer, index=True, nullable=False),
                 Column("user_id", String(64), index=True, nullable=False),
                 Column("n", Integer, nullable=False),               # v1, v2, ...
                 Column("created_at", DateTime, nullable=False),
                 Column("source", String(16), nullable=False),       # draft | edit | chat | fix | restore
                 Column("note", Text),
                 Column("spec_json", Text, nullable=False),
                 Column("n_samples", Integer),
                 Column("n_patients", Integer),
                 Column("verdict", String(32)))

llm_cache = Table("llm_cache", meta,
                  Column("key", String(64), primary_key=True),
                  Column("created_at", DateTime, nullable=False),
                  Column("text", Text, nullable=False),
                  Column("usage_json", Text))

ai_usage = Table("ai_usage", meta,                               # AI requests and estimated spend per UTC day
                 Column("day", String(10), primary_key=True),        # 2026-11-14
                 Column("key", String(160), primary_key=True),       # global | caller:<who> | ip:<address>
                 Column("calls", Integer, nullable=False),
                 Column("cost_usd", Float, nullable=False))


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _iso(dt):
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


class LLMCache:
    """The cache interface LLMClient expects: get(key) -> (text, usage) or None; set(key, text, usage)."""

    def __init__(self, engine):
        self.engine = engine

    def get(self, key):
        with self.engine.connect() as c:
            row = c.execute(select(llm_cache.c.text, llm_cache.c.usage_json).where(llm_cache.c.key == key)).first()
        return (row.text, json.loads(row.usage_json or "{}")) if row else None

    def set(self, key, text, usage):
        with self.engine.begin() as c:
            if not c.execute(select(llm_cache.c.key).where(llm_cache.c.key == key)).first():
                c.execute(insert(llm_cache).values(key=key, created_at=_now(), text=text, usage_json=json.dumps(usage)))


class Storage:
    def __init__(self, url):
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        if url.startswith("sqlite:///"):
            pathlib.Path(url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
        self.url = url
        self.engine = create_engine(url, future=True, pool_pre_ping=True)
        meta.create_all(self.engine)
        self.llm_cache = LLMCache(self.engine)

    @property
    def kind(self):
        return self.engine.dialect.name

    def ensure_user(self, user):
        with self.engine.begin() as c:
            if not c.execute(select(users.c.id).where(users.c.id == user)).first():
                c.execute(insert(users).values(id=user, created_at=_now()))

    # ---------- searches ----------
    @staticmethod
    def _search_row(row, with_spec=False):
        out = {"id": row.id, "query": row.query, "mode": row.mode, "created_at": _iso(row.created_at),
               "updated_at": _iso(row.updated_at), "n_samples": row.n_samples, "n_patients": row.n_patients,
               "verdict": row.verdict, "reason": row.reason, "draft_id": row.draft_id}
        if with_spec:
            out["spec"] = json.loads(row.spec_json)
        return out

    def add_search(self, user, query, mode, spec, summary, draft_id=None):
        self.ensure_user(user)
        now = _now()
        with self.engine.begin() as c:
            res = c.execute(insert(searches).values(user_id=user, created_at=now, updated_at=now, query=query, mode=mode,
                                                    spec_json=json.dumps(spec), draft_id=draft_id, **summary))
            return int(res.inserted_primary_key[0])

    def update_search(self, user, search_id, spec, summary):
        with self.engine.begin() as c:
            c.execute(update(searches).where((searches.c.id == search_id) & (searches.c.user_id == user))
                      .values(updated_at=_now(), spec_json=json.dumps(spec), **summary))

    def get_search(self, user, search_id):
        with self.engine.connect() as c:
            row = c.execute(select(searches).where((searches.c.id == search_id) & (searches.c.user_id == user))).first()
        return self._search_row(row, with_spec=True) if row else None

    def list_searches(self, user, limit=30):
        with self.engine.connect() as c:
            rows = c.execute(select(searches).where(searches.c.user_id == user)
                             .order_by(searches.c.updated_at.desc()).limit(limit)).all()
        return [self._search_row(r) for r in rows]

    def delete_search(self, user, search_id):
        with self.engine.begin() as c:
            c.execute(delete(versions).where((versions.c.search_id == search_id) & (versions.c.user_id == user)))
            return c.execute(delete(searches).where((searches.c.id == search_id) & (searches.c.user_id == user))).rowcount

    def recent_queries(self, user, limit=50):
        seen, out = set(), []
        for s in self.list_searches(user, limit):
            if s["query"].lower() not in seen:
                seen.add(s["query"].lower())
                out.append(s["query"])
        return out

    # ---------- versions ----------
    @staticmethod
    def _version_row(r, with_spec=False):
        out = {"n": r.n, "created_at": _iso(r.created_at), "source": r.source, "note": r.note,
               "n_samples": r.n_samples, "n_patients": r.n_patients, "verdict": r.verdict}
        if with_spec:
            out["spec"] = json.loads(r.spec_json)
        return out

    def add_version(self, user, search_id, spec, summary, source, note=""):
        with self.engine.begin() as c:
            last = c.execute(select(versions.c.n).where((versions.c.search_id == search_id) & (versions.c.user_id == user))
                             .order_by(versions.c.n.desc()).limit(1)).first()
            n = (last.n if last else 0) + 1
            c.execute(insert(versions).values(search_id=search_id, user_id=user, n=n, created_at=_now(), source=source,
                                              note=note, spec_json=json.dumps(spec), n_samples=summary.get("n_samples"),
                                              n_patients=summary.get("n_patients"), verdict=summary.get("verdict")))
        return n

    def list_versions(self, user, search_id):
        with self.engine.connect() as c:
            rows = c.execute(select(versions).where((versions.c.search_id == search_id) & (versions.c.user_id == user))
                             .order_by(versions.c.n)).all()
        return [self._version_row(r) for r in rows]

    def get_version(self, user, search_id, n):
        with self.engine.connect() as c:
            r = c.execute(select(versions).where((versions.c.search_id == search_id) & (versions.c.user_id == user)
                                                 & (versions.c.n == n))).first()
        return self._version_row(r, with_spec=True) if r else None

    # ---------- favourites ----------
    def add_favorite(self, user, label, query, spec=None, search_id=None):
        self.ensure_user(user)
        with self.engine.begin() as c:
            existing = c.execute(select(favorites.c.id).where((favorites.c.user_id == user) & (favorites.c.query == query))).first()
            if existing:
                c.execute(update(favorites).where(favorites.c.id == existing.id)
                          .values(label=label, spec_json=json.dumps(spec) if spec else None, search_id=search_id))
                return int(existing.id)
            res = c.execute(insert(favorites).values(user_id=user, created_at=_now(), label=label, query=query,
                                                     spec_json=json.dumps(spec) if spec else None, search_id=search_id))
            return int(res.inserted_primary_key[0])

    def list_favorites(self, user):
        with self.engine.connect() as c:
            rows = c.execute(select(favorites).where(favorites.c.user_id == user).order_by(favorites.c.created_at.desc())).all()
        return [{"id": r.id, "label": r.label, "query": r.query, "has_spec": bool(r.spec_json), "search_id": r.search_id,
                 "created_at": _iso(r.created_at)} for r in rows]

    def get_favorite(self, user, fav_id):
        with self.engine.connect() as c:
            r = c.execute(select(favorites).where((favorites.c.id == fav_id) & (favorites.c.user_id == user))).first()
        if not r:
            return None
        return {"id": r.id, "label": r.label, "query": r.query, "spec": json.loads(r.spec_json) if r.spec_json else None}

    def delete_favorite(self, user, fav_id):
        with self.engine.begin() as c:
            return c.execute(delete(favorites).where((favorites.c.id == fav_id) & (favorites.c.user_id == user))).rowcount

    # ---------- AI usage (limits.py) ----------
    def usage(self, day, key):
        with self.engine.connect() as c:
            row = c.execute(select(ai_usage.c.calls, ai_usage.c.cost_usd)
                            .where((ai_usage.c.day == day) & (ai_usage.c.key == key))).first()
        return {"calls": int(row.calls), "cost": float(row.cost_usd)} if row else {"calls": 0, "cost": 0.0}

    def add_usage(self, day, key, calls, cost):
        where = (ai_usage.c.day == day) & (ai_usage.c.key == key)
        for attempt in range(2):           # two first requests of the day at once: the loser updates the winner's row
            try:
                with self.engine.begin() as c:
                    if c.execute(select(ai_usage.c.key).where(where)).first():
                        c.execute(update(ai_usage).where(where).values(calls=ai_usage.c.calls + calls,
                                                                       cost_usd=ai_usage.c.cost_usd + cost))
                    else:
                        c.execute(insert(ai_usage).values(day=day, key=key, calls=calls, cost_usd=cost))
                return
            except IntegrityError:
                if attempt:
                    raise

    # ---------- drafts ----------
    def save_draft(self, user, query, draft):
        self.ensure_user(user)
        draft_id = str(uuid.uuid4())
        with self.engine.begin() as c:
            c.execute(insert(drafts).values(id=draft_id, user_id=user, created_at=_now(), query=query,
                                            draft_json=json.dumps(draft, default=list)))
        return draft_id

    def get_draft(self, user, draft_id):
        with self.engine.connect() as c:
            row = c.execute(select(drafts).where((drafts.c.id == draft_id) & (drafts.c.user_id == user))).first()
        return json.loads(row.draft_json) if row else None
