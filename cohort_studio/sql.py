"""A SQL view of a cohort spec (DuckDB dialect): the steps the engine runs, written as one CTE per step.

The engine (pandas, cohort_studio/engine.py) stays the source of truth. This module translates the same steps into
SQL someone can read, copy and run, and `verify()` runs that SQL in DuckDB on the same frozen files and compares
every step's rows, samples and patients, and the final sample IDs, with the engine's ledger. A difference is
reported, never hidden.

    plan = to_sql(spec, store)        # Plan: sources (CREATE ... statements) + one query with a CTE per step
    plan.text()                       # the SQL to show or copy (paths relative to the repository root)
    verify(plan, store, engine_result)

The translation keeps the engine's rules:
  * a value TCGA marks as missing ('[Not Available]', 'Indeterminate', ...) never passes a filter (is_missing());
  * a 'rows' join is an inner join (one row per match: it can inflate), an 'exists' join a semi-join;
  * a combined condition ('where') uses three-valued logic, which SQL's NULL already follows (unknown = NULL),
    and keeps a row only when the whole condition IS TRUE.
"""
import gzip
import hashlib
import pathlib
import re
import threading
import time

from . import ops
from .engine import PREFIX, extra_cohort_columns
from .spec import resolve_condition

REPO = pathlib.Path(__file__).resolve().parent.parent
LAYER_COLUMNS = ops.LAYER_COLUMNS
MISSING_TOKENS = sorted(ops.MISSING_TOKENS)


def lit(value):
    """A SQL string literal."""
    return "'" + str(value).replace("'", "''") + "'"


def ident(name):
    """A quoted SQL identifier (column names like "clin.pathologic_stage" keep the engine's own names)."""
    return '"' + str(name).replace('"', '""') + '"'


def _as_list(value):
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple, set)) else [value]


def _num(value):
    return repr(float(value)) if float(value) != int(float(value)) else str(int(float(value)))


def _header(path, sep):
    """The column names in a CSV/TSV file's first line (gzip or plain)."""
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        line = f.readline().rstrip("\r\n")
    return [c.strip().strip('"') for c in line.split(sep)]


class Plan:
    """sources: [(name, sql)] statements that load the files; ctes: [{name, index, sql}]; query: the final SELECT."""

    def __init__(self, sources, ctes, final, notes, data_dir, rel_dir):
        self.sources, self.ctes, self.final, self.notes = sources, ctes, final, notes
        self.data_dir, self.rel_dir = data_dir, rel_dir

    def query(self):
        body = ",\n".join(f"{c['name']} AS (\n{c['sql']}\n)" for c in self.ctes)
        return (f"WITH\n{body}\n{self.final}" if self.ctes else self.final) + ";"

    def counts_query(self):
        """Rows, samples and patients after every step (to compare with the engine's ledger)."""
        parts = [f"SELECT {c['index']} AS step, count(*) AS rows, count(DISTINCT sample_id) AS samples, "
                 f"count(DISTINCT patient_id) AS patients FROM {c['name']}" for c in self.ctes if c.get("index") is not None]
        body = ",\n".join(f"{c['name']} AS (\n{c['sql']}\n)" for c in self.ctes)
        union = "\nUNION ALL\n".join(parts) or "SELECT NULL AS step, 0 AS rows, 0 AS samples, 0 AS patients WHERE FALSE"
        return f"WITH\n{body}\n{union}\nORDER BY step;" if self.ctes else union

    def source_text(self, absolute=False):
        return "\n\n".join(self._path(sql, absolute) for _, sql in self.sources)

    def _path(self, sql, absolute):
        return sql if not absolute else sql.replace("@DATA@", self.data_dir.as_posix())

    def text(self):
        """The whole script: header, sources, then the cohort query (paths relative to the repository root)."""
        notes = "".join(f"-- Note: {n}\n" for n in self.notes)
        head = ("-- Cohort Studio: the cohort as SQL (DuckDB). One CTE per step, in the order the engine runs them.\n"
                "-- Run it from the repository root, e.g.  duckdb -c \".read cohort.sql\"  (needs fetch.py's files).\n"
                "-- Missing values ('[Not Available]', 'Indeterminate', ...) never pass a filter, as in the app.\n" + notes)
        sources = self.source_text().replace("@DATA@", self.rel_dir)
        return f"{head}\n-- ── Sources ──────────────────────────────────────────────\n{sources}\n\n" \
               f"-- ── Cohort ───────────────────────────────────────────────\n{self.query()}\n"

    def line_map(self):
        """Where each step's CTE starts and ends in text() (1-based lines), for highlighting the selected step."""
        lines = self.text().split("\n")
        out = {}
        for c in self.ctes:
            if c.get("index") is None:
                continue
            start = next((i for i, l in enumerate(lines) if l.startswith(f"{c['name']} AS (")), None)
            if start is None:
                continue
            end = start
            while end < len(lines) and lines[end] != ")" and not lines[end].startswith("),"):
                end += 1
            out[c["index"]] = [start + 1, end + 1]
        return out


class _Builder:
    def __init__(self, spec, store):
        self.spec, self.store = spec, store
        self.data_dir = pathlib.Path(store.data_dir).resolve()
        try:
            self.rel_dir = self.data_dir.relative_to(REPO).as_posix()
        except ValueError:
            self.rel_dir = self.data_dir.as_posix()
        self.sources, self.ctes, self.notes = {}, [], []
        self.columns = None           # the frame's columns after the last CTE
        self.helpers = 0

    # ---------- sources ----------
    def path(self, *parts):
        return "@DATA@/" + "/".join(parts)

    def need(self, name):
        if name in self.sources:
            return name
        if name == "macros":
            tokens = ", ".join(lit(t) for t in MISSING_TOKENS)
            self.sources[name] = (
                "-- TCGA marks missing values in several ways; none of them passes a filter.\n"
                "CREATE OR REPLACE MACRO is_missing(x) AS (\n"
                f"  x IS NULL OR starts_with(trim(CAST(x AS VARCHAR)), '[') OR lower(trim(CAST(x AS VARCHAR))) IN ({tokens})\n);\n"
                "-- 'Stage IIA' -> 'II' (NULL when the stage is missing).\n"
                "CREATE OR REPLACE MACRO stage_group(x) AS (\n"
                "  CASE WHEN is_missing(x) THEN NULL\n"
                "       ELSE nullif(upper(regexp_extract(lower(x), 'stage\\s*(iv|iii|ii|i)', 1)), '') END\n);")
        elif name == "manifest":
            cols = _header(self.data_dir / "sample_manifest.csv", ",")
            sel = []
            for c in cols:
                if c in LAYER_COLUMNS.values():
                    sel.append(f"lower(trim({ident(c)})) = 'true' AS {ident(c)}")
                elif c == "slides_for_sample":
                    sel.append(f"coalesce(TRY_CAST({ident(c)} AS INTEGER), 0) AS {ident(c)}")
                else:
                    sel.append(ident(c))
            self.sources[name] = (
                "-- One row per sample: patient, sample type and which data layers exist.\n"
                "CREATE OR REPLACE TABLE manifest AS\nSELECT " + ",\n       ".join(sel) +
                f"\nFROM read_csv({lit(self.path('sample_manifest.csv'))}, all_varchar = true);")
        elif name == "slides":
            self.sources[name] = (
                "-- One row per pathology slide (several per sample).\n"
                "CREATE OR REPLACE TABLE slides AS\nSELECT slide_barcode, sample_id, patient_id, slide_kind\n"
                f"FROM read_csv({lit(self.path('slide_manifest.csv'))}, all_varchar = true);")
        elif name in ("clinical", "survival"):
            fname = {"clinical": "clinical.tsv", "survival": "survival.tsv"}[name]
            cols = _header(self.data_dir / "raw" / fname, "\t")
            sid = _first_of(cols, ["sampleID", "sample", "sample_id", "Sample"])
            pid = _first_of(cols, ["_PATIENT", "patient_id", "bcr_patient_barcode"])
            drop = [c for c in (sid, "sample_id", "patient_id") if c in cols]
            patient = f"left({ident(pid)}, 12)" if pid else f"left({ident(sid)}, 12)"
            excl = f" EXCLUDE ({', '.join(ident(c) for c in dict.fromkeys(drop))})" if drop else ""
            what = "clinical record per sample" if name == "clinical" else "curated survival record per sample (TCGA-CDR)"
            self.sources[name] = (
                f"-- One {what}.\n"
                f"CREATE OR REPLACE TABLE {name} AS\nSELECT left({ident(sid)}, 15) AS sample_id, {patient} AS patient_id, *{excl}\n"
                f"FROM read_csv({lit(self.path('raw', fname))}, delim = '\\t', all_varchar = true, header = true);")
        elif name.startswith("mutation_"):
            gene = name[len("mutation_"):]
            path = self.data_dir / "raw" / "mutation_gene_level.tsv.gz"
            first = (_header(path, "\t") or [""])[0] or "column0"
            self.sources[name] = (
                f"-- Gene-level MC3 calls for {gene} (the file is genes x samples; one row per assessed sample here).\n"
                f"CREATE OR REPLACE TABLE {ident(name)} AS\n"
                "SELECT left(col, 15) AS sample_id, TRY_CAST(v AS DOUBLE) AS call\n"
                f"FROM (UNPIVOT (SELECT * FROM read_csv({lit(self.path('raw', 'mutation_gene_level.tsv.gz'))}, delim = '\\t', "
                f"all_varchar = true, header = true)\n             WHERE {ident(first)} = {lit(gene)})\n"
                f"      ON COLUMNS(* EXCLUDE ({ident(first)})) INTO NAME col VALUE v);")
        return name

    # ---------- predicates ----------
    def predicate(self, col, cmp, value, frame=True):
        """The filter test on a value expression `col` (frame=True: the engine's filter_column semantics)."""
        self.need("macros")
        vals = _as_list(value)
        if cmp == "notnull":
            return f"NOT is_missing({col})"
        if cmp in ("in", "not_in"):
            wanted = ", ".join(lit(str(v).strip().lower()) for v in vals) or "NULL"
            neg = "NOT " if cmp == "not_in" else ""
            return f"NOT is_missing({col}) AND lower(trim({col})) {neg}IN ({wanted})"
        if cmp == "contains":
            words = " OR ".join(f"contains(lower({col}), {lit(str(v).strip().lower())})" for v in vals) or "FALSE"
            return f"NOT is_missing({col}) AND ({words})"
        if cmp == "stage_in":
            wanted = ", ".join(lit(str(v).upper()) for v in vals) or "NULL"
            return f"coalesce(stage_group({col}) IN ({wanted}), FALSE)"
        if cmp == "gte":
            return f"coalesce(TRY_CAST({col} AS DOUBLE) >= {_num(value)}, FALSE)"
        if cmp == "lte":
            return f"coalesce(TRY_CAST({col} AS DOUBLE) <= {_num(value)}, FALSE)"
        if cmp == "between":
            return f"coalesce(TRY_CAST({col} AS DOUBLE) BETWEEN {_num(vals[0])} AND {_num(vals[1])}, FALSE)"
        if cmp == "is_event":
            return f"coalesce(TRY_CAST({col} AS DOUBLE) = 1, FALSE)"
        if cmp == "in_raw":
            wanted = ", ".join(lit(str(v).strip().lower()) for v in vals) or "NULL"
            return f"{col} IS NOT NULL AND lower(trim({col})) IN ({wanted})"
        raise ValueError(f"Unknown comparison '{cmp}'")

    def condition(self, node, idcol):
        """A combined condition as a three-valued SQL expression (TRUE / FALSE / NULL = unknown) for one ID column."""
        for key, op in (("all", " AND "), ("any", " OR ")):
            if key in node:
                return "(" + op.join(self.condition(k, idcol) for k in node[key]) + ")"
        if "not" in node:
            return f"(NOT {self.condition(node['not'], idcol)})"
        if "layer" in node:
            self.need("manifest")
            col = ident(LAYER_COLUMNS[node["layer"]])
            return f"coalesce((SELECT first(m.{col}) FROM manifest m WHERE m.sample_id = {idcol}), FALSE)"
        if "sample_type" in node:
            self.need("manifest")
            types = ", ".join(lit(t) for t in _as_list(node["sample_type"])) or "NULL"
            return f"coalesce((SELECT first(m.sample_type) FROM manifest m WHERE m.sample_id = {idcol}) IN ({types}), FALSE)"
        if "gene" in node:
            table = ident(self.need(f"mutation_{node['gene']}"))
            test = "> 0" if node.get("mutated", True) else "= 0"
            return f"(SELECT first(g.call) {test} FROM {table} g WHERE g.sample_id = {idcol})"
        if "slide_kinds" in node:
            self.need("slides")
            kinds = _as_list(node["slide_kinds"])
            where = f" WHERE slide_kind IN ({', '.join(lit(k) for k in kinds)})" if kinds else ""
            return f"coalesce({idcol} IN (SELECT sample_id FROM slides{where}), FALSE)"
        table = self.need(node["table"])
        self.need("macros")
        v = f"(SELECT first(t.{ident(node['column'])}) FROM {table} t WHERE t.sample_id = {idcol})"
        cmp = node["cmp"]
        if cmp == "notnull":
            return f"(NOT is_missing({v}))"
        if cmp == "in_raw":
            wanted = ", ".join(lit(str(x).strip().lower()) for x in _as_list(node.get("value"))) or "NULL"
            return (f"(CASE WHEN {v} IS NOT NULL AND lower(trim({v})) IN ({wanted}) THEN TRUE "
                    f"WHEN {v} IS NULL OR trim({v}) = '' OR starts_with(trim({v}), '[') THEN NULL ELSE FALSE END)")
        return f"(CASE WHEN is_missing({v}) THEN NULL ELSE ({self.predicate(v, cmp, node.get('value'))}) END)"

    # ---------- steps ----------
    def needed_columns(self, table):
        """Real columns of `table` later steps read from the frame (so a 'rows' join brings just those)."""
        cols = []
        for s in self.spec.get("steps", []):
            if s.get("op") == "filter" and s.get("table") == table:
                cols.append(self.store.resolve_field(table, s["field"]))
            elif s.get("op") == "any_of" and s.get("table") == table:
                cols += [self.store.resolve_field(table, c["field"]) for c in s.get("conditions", [])]
            elif s.get("op") == "one_per_patient" and s.get("rule") in ("earliest", "latest") and table == "clinical":
                cols.append(self.store.resolve_field("clinical", "collection_day"))
        return [c for c in dict.fromkeys(cols) if c]

    def add(self, sql, index, label):
        name = f"s{len([c for c in self.ctes if c.get('index') is not None]) + 1}_{re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_')[:28]}"
        self.ctes.append({"name": name, "index": index, "sql": sql})
        return name

    def helper(self, sql, label):
        self.helpers += 1
        name = f"h{self.helpers}_{label}"
        self.ctes.append({"name": name, "index": None, "sql": sql})
        return name

    def step(self, i, step, prev):
        op = step["op"]
        ind = "  "
        if op == "sample_type":
            types = ", ".join(lit(t) for t in step.get("values", [])) or "NULL"
            return self.add(f"{ind}SELECT * FROM {prev}\n{ind}WHERE sample_type IN ({types})", i, "sample_type")
        if op in ("layer", "exclude_layer"):
            col = ident(LAYER_COLUMNS[step["layer"]])
            neg = "NOT " if op == "exclude_layer" else ""
            return self.add(f"{ind}SELECT * FROM {prev}\n{ind}WHERE {neg}{col}", i, f"{'no_' if neg else 'has_'}{step['layer']}")
        if op == "join":
            return self.join(i, step, prev)
        if op == "filter":
            col = ident(f"{PREFIX[step['table']]}.{self.store.resolve_field(step['table'], step['field'])}")
            return self.add(f"{ind}SELECT * FROM {prev}\n{ind}WHERE {self.predicate(col, step['cmp'], step.get('value'))}", i,
                            f"filter_{step['field']}")
        if op == "any_of":
            p = PREFIX[step["table"]]
            ors = "\n" + ind + "   OR ".join(
                f"({self.predicate(ident(f'{p}.' + self.store.resolve_field(step['table'], c['field'])), c['cmp'], c.get('value'))})\n"
                for c in step["conditions"])
            return self.add(f"{ind}SELECT * FROM {prev}\n{ind}WHERE{ors.rstrip()}", i, "either_or")
        if op == "mutation":
            table = ident(self.need(f"mutation_{step['gene']}"))
            test = "call > 0" if step.get("mutated", True) else "call = 0"
            return self.add(f"{ind}SELECT * FROM {prev}\n{ind}WHERE sample_id IN (SELECT sample_id FROM {table} WHERE {test})", i,
                            f"{step['gene']}_{'mut' if step.get('mutated', True) else 'wt'}")
        if op == "dedupe":
            return self.add(f"{ind}SELECT * FROM {prev}\n{ind}QUALIFY row_number() OVER (PARTITION BY sample_id) = 1", i,
                            "one_row_per_sample")
        if op == "one_per_patient":
            return self.one_per_patient(i, step, prev)
        if op == "where":
            cond = resolve_condition(step["condition"], self.store)
            cols = {"sample": ["sample_id"], "added_normals": ["normal.sample_id"],
                    "both": ["sample_id", "normal.sample_id"]}[step.get("apply_to", "sample")]
            tests = f"\n{ind}  AND ".join(f"{self.condition(cond, f'{prev}.{ident(c)}')} IS TRUE" for c in cols)
            return self.add(f"{ind}SELECT * FROM {prev}\n{ind}WHERE {tests}", i, "condition")
        raise ValueError(f"Unknown step '{op}'")

    def join(self, i, step, prev):
        table, key, mode = step["table"], step.get("key", "sample_id"), step.get("mode", "rows")
        p = PREFIX[table]
        ind = "  "
        if table == "slides":
            self.need("slides")
            kinds = _as_list(step.get("slide_kinds"))
            right = f"(SELECT * FROM slides WHERE slide_kind IN ({', '.join(lit(k) for k in kinds)}))" if kinds else "slides"
            bring = ["slide_barcode", "slide_kind", "sample_id"]
        elif table == "normals":
            self.need("manifest")
            layers = "".join(f" AND {ident(LAYER_COLUMNS[l])}" for l in step.get("require_layers") or [])
            right = f"(SELECT sample_id, patient_id FROM manifest WHERE sample_type = 'Solid Tissue Normal'{layers})"
            bring = ["sample_id", "patient_id"]
        else:
            right = self.need(table)
            bring = ["sample_id"] + self.needed_columns(table)
        label = f"join_{table}" + ("_exists" if mode == "exists" else "")
        if mode == "exists":
            return self.add(f"{ind}-- once per sample if it has at least one match (a semi-join: never adds rows)\n"
                            f"{ind}SELECT * FROM {prev}\n{ind}WHERE {ident(key)} IN (SELECT {ident(key)} FROM {right})", i, label)
        new = [f"{p}.{c}" for c in dict.fromkeys(bring)]
        old = [c for c in new if c in self.columns]
        keep = f"{prev}.* EXCLUDE ({', '.join(ident(c) for c in old)})" if old else f"{prev}.*"
        cols = ",\n         ".join(f"r.{ident(c)} AS {ident(f'{p}.{c}')}" for c in dict.fromkeys(bring))
        self.columns |= set(new)
        return self.add(f"{ind}-- one row per match: a sample with several matches becomes several rows\n"
                        f"{ind}SELECT {keep},\n         {cols}\n{ind}FROM {prev}\n{ind}JOIN {right} r ON r.{ident(key)} = {prev}.{ident(key)}",
                        i, label)

    def one_per_patient(self, i, step, prev):
        rule = step.get("rule", "priority")
        order = list(step.get("order") or ops.SAMPLE_TYPE_PRIORITY)
        rank = "CASE sample_type " + " ".join(f"WHEN {lit(t)} THEN {k}" for k, t in enumerate(order)) + f" ELSE {len(order)} END"
        day_sel, day_order = "", ""
        if rule in ("earliest", "latest"):
            col = ident(f"{PREFIX['clinical']}.{self.store.resolve_field('clinical', 'collection_day')}")
            day_sel = f", first(TRY_CAST({col} AS DOUBLE)) AS day"
            day_order = f"day {'ASC' if rule == 'earliest' else 'DESC'} NULLS LAST, "
        pick = self.helper(
            f"  SELECT sample_id\n"
            f"  FROM (SELECT sample_id, first(patient_id) AS patient_id, first(sample_type) AS sample_type{day_sel}\n"
            f"        FROM {prev} GROUP BY sample_id)\n"
            f"  QUALIFY row_number() OVER (PARTITION BY patient_id ORDER BY {day_order}{rank}, sample_id) = 1",
            f"pick_{i + 1}")
        return self.add(f"  -- keep every row of the one sample chosen per patient ({rule}"
                        f"{': ' + ' > '.join(order) if rule == 'priority' else ''})\n"
                        f"  SELECT * FROM {prev}\n  WHERE sample_id IN (SELECT sample_id FROM {pick})", i, "one_per_patient")

    def build(self):
        self.need("manifest")
        self.columns = set(_header(self.data_dir / "sample_manifest.csv", ","))
        prev = self.helper("  SELECT * FROM manifest", "all_samples")
        for i, step in enumerate(self.spec.get("steps", [])):
            prev = self.step(i, step, prev)
        extra = extra_cohort_columns(self.spec)
        picks = [f"  SELECT sample_id FROM {prev}"] + [f"  SELECT {ident(c)} FROM {prev}  -- added to the cohort" for c in extra]
        final = ("SELECT DISTINCT sample_id\nFROM (\n" + "\n  UNION ALL\n".join(picks) + "\n) AS cohort\n"
                 "WHERE sample_id IS NOT NULL\nORDER BY sample_id")
        order = ["macros", "manifest", "slides", "clinical", "survival"]
        sources = [(n, self.sources[n]) for n in order if n in self.sources] + \
                  [(n, s) for n, s in self.sources.items() if n not in order]
        return Plan(sources, self.ctes, final, self.notes, self.data_dir, self.rel_dir)


def _first_of(cols, names):
    return next((n for n in names if n in cols), None)


def to_sql(spec, store):
    """Translate a spec (the steps the engine will run) into a Plan. Raises ValueError for a step it can't express."""
    return _Builder(spec, store).build()


# ---------------------------------------------------------------- running it in DuckDB
_CONNECTIONS = {}
_LOCK = threading.Lock()


def available():
    try:
        import duckdb  # noqa: F401
        return True
    except ImportError:
        return False


def _connection(data_dir):
    import duckdb
    key = str(pathlib.Path(data_dir).resolve())
    if key not in _CONNECTIONS:
        _CONNECTIONS[key] = {"con": duckdb.connect(), "done": set()}
    return _CONNECTIONS[key]


def run_plan(plan, extra_sql=None):
    """Run the plan's sources (once per server, cached) and its query. Returns (sample_ids, counts, ms)."""
    t = time.time()
    with _LOCK:
        entry = _connection(plan.data_dir)
        con = entry["con"]
        for _, sql in plan.sources:
            text = plan._path(sql, absolute=True)
            digest = hashlib.sha1(text.encode()).hexdigest()
            if digest not in entry["done"]:
                con.execute(text)
                entry["done"].add(digest)
        ids = [r[0] for r in con.execute(plan.query()).fetchall()]
        counts = {int(r[0]): {"rows": int(r[1]), "samples": int(r[2]), "patients": int(r[3])}
                  for r in con.execute(plan.counts_query()).fetchall() if r[0] is not None}
    return ids, counts, int(1000 * (time.time() - t))


def verify(plan, result):
    """Run the SQL in DuckDB and compare it with the engine's result (a RunResult for the same spec)."""
    if not available():
        return {"ok": None, "reason": "DuckDB isn't installed on this server (pip install duckdb), so the SQL wasn't run."}
    if not result.ok:
        return {"ok": None, "reason": "Some steps don't run on this release, so there is nothing to compare yet."}
    try:
        ids, counts, ms = run_plan(plan)
    except Exception as exc:  # show the database's own message instead of failing the page
        return {"ok": False, "reason": f"DuckDB couldn't run the SQL: {str(exc).splitlines()[0][:300]}", "steps": []}
    steps = []
    for r in result.steps:
        mine = {"rows": r.rows_out, "samples": r.samples_out, "patients": r.patients_out}
        theirs = counts.get(r.index)
        steps.append({"index": r.index, "engine": mine, "sql": theirs, "match": theirs == mine})
    engine_ids = sorted(result.cohort_ids)
    same = sorted(ids) == engine_ids
    a, b = set(engine_ids), set(ids)
    ok = same and all(s["match"] for s in steps)
    return {"ok": ok, "ms": ms, "steps": steps,
            "cohort": {"engine": len(engine_ids), "sql": len(ids), "match": same,
                       "only_engine": sorted(a - b)[:5], "only_sql": sorted(b - a)[:5]},
            "reason": "" if ok else ("The final sample IDs differ." if not same else
                                     "A step's row, sample or patient count differs.")}
