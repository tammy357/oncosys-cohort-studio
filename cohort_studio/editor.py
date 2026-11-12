"""Convert spec steps to and from the editable table in the app."""
import pandas as pd

COLUMNS = ["step", "table", "what", "key", "mode", "compare", "value", "slide kinds", "add to cohort"]
STEP_TYPES = ["sample_type", "layer", "join", "filter", "mutation", "dedupe", "one_per_patient"]


def _join_list(values):
    return ", ".join(str(v) for v in values) if isinstance(values, (list, tuple)) else ("" if values is None else str(values))


def steps_to_table(steps):
    rows = []
    for s in steps:
        op = s.get("op", "")
        row = {c: "" for c in COLUMNS}
        row["step"] = op
        row["add to cohort"] = bool(s.get("add_to_cohort", False))
        if op == "sample_type":
            row["what"] = _join_list(s.get("values", []))
        elif op == "layer":
            row["what"] = s.get("layer", "")
        elif op == "join":
            row.update({"table": s.get("table", ""), "key": s.get("key", "sample_id"), "mode": s.get("mode", "rows")})
            if s.get("table") == "slides":
                row["slide kinds"] = _join_list(s.get("slide_kinds") or []) or "any"
        elif op == "filter":
            row.update({"table": s.get("table", ""), "what": s.get("field", ""), "compare": s.get("cmp", "")})
            v = s.get("value")
            row["value"] = (f"{v[0]}-{v[1]}" if s.get("cmp") == "between" and isinstance(v, (list, tuple)) else _join_list(v))
        elif op == "mutation":
            row["what"] = s.get("gene", "")
            row["value"] = "mutated" if s.get("mutated", True) else "not mutated"
        elif op == "one_per_patient":
            row["what"] = s.get("rule", "priority")
            row["value"] = _join_list(s.get("order") or []) if s.get("rule", "priority") == "priority" else ""
        rows.append(row)
    return pd.DataFrame(rows, columns=COLUMNS)


def _split(text):
    return [p.strip() for p in str(text or "").split(",") if p.strip()]


def _number(text):
    f = float(text)
    return int(f) if f.is_integer() else f


def table_to_steps(df):
    """Read the edited table back into steps. Returns (steps, problems)."""
    steps, problems = [], []
    for i, row in df.fillna("").iterrows():
        op = str(row.get("step", "")).strip()
        if not op:
            continue
        try:
            if op == "sample_type":
                steps.append({"op": op, "values": _split(row["what"])})
            elif op == "layer":
                steps.append({"op": op, "layer": str(row["what"]).strip()})
            elif op == "join":
                st = {"op": op, "table": str(row["table"]).strip(), "key": str(row["key"]).strip() or "sample_id",
                      "mode": str(row["mode"]).strip() or "rows"}
                if st["table"] == "slides":
                    kinds = str(row["slide kinds"]).strip()
                    st["slide_kinds"] = [] if kinds in ("", "any") else _split(kinds)
                if st["table"] == "normals":
                    st["add_to_cohort"] = bool(row["add to cohort"])
                steps.append(st)
            elif op == "filter":
                cmp = str(row["compare"]).strip()
                raw = str(row["value"]).strip()
                if cmp in ("gte", "lte"):
                    value = _number(raw)
                elif cmp == "between":
                    lo, hi = raw.replace("to", "-").split("-")
                    value = [_number(lo), _number(hi)]
                elif cmp in ("notnull", "is_event"):
                    value = None
                else:
                    value = _split(raw)
                steps.append({"op": op, "table": str(row["table"]).strip(), "field": str(row["what"]).strip(),
                              "cmp": cmp, "value": value})
            elif op == "mutation":
                steps.append({"op": op, "gene": str(row["what"]).strip().upper(),
                              "mutated": str(row["value"]).strip().lower() not in ("not mutated", "false", "no", "wild-type")})
            elif op == "dedupe":
                steps.append({"op": op})
            elif op == "one_per_patient":
                rule = str(row["what"]).strip() or "priority"
                st = {"op": op, "rule": rule}
                if rule == "priority" and _split(row["value"]):
                    st["order"] = _split(row["value"])
                steps.append(st)
            else:
                problems.append(f"Row {i + 1}: unknown step type '{op}'.")
        except Exception as exc:
            problems.append(f"Row {i + 1}: couldn't read this row ({exc}).")
    return steps, problems
