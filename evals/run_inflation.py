"""Join-inflation suite: does each route show where a count inflates, and export the right samples after the fix?

    python evals/run_inflation.py                         # built-in parser
    python evals/run_inflation.py --parser rules ai       # + the AI route (key from the environment, or --env-file)
    python evals/run_inflation.py --parser ai --env-file .env --model gpt-4.1-mini
    python evals/run_inflation.py --freeze                # rewrite each case's 'expect' from its gold steps

For every case (evals/inflation_cases.json) and route:
  1. the route turns the request into steps;
  2. the engine runs them WITHOUT the fix: which steps inflate (rows grow faster than distinct samples)?
  3. the fix is applied and the cohort exported, exactly like `python -m cohort_studio` (evals/harness.build_cohort);
  4. the export is compared with the gold steps' export, and checked for duplicate sample IDs.

Outcomes:
  shown + fixed   the route's steps inflate where the gold steps do, the tool flags it, the export is right  (the demo)
  avoided         no inflation shown because the route already joined once per sample; export right
  wrong cohort    inflation shown and fixed, but different samples (a different reading of the request)
  missed          the export is wrong and nothing inflated
  clean / false alarm   controls: nothing may be flagged
"""
import argparse
import json
import os
import pathlib
import sys

from harness import EVALS, build_cohort

from cohort_studio import engine, exports
from cohort_studio.data import DataStore
from cohort_studio.parse import parse_request

CASES = EVALS / "inflation_cases.json"


def inflated_steps(spec, store):
    r = engine.run(spec, store)
    return [{"step": s.index + 1, "op": s.op, "rows": s.rows_out, "samples": s.samples_out} for s in r.steps if s.inflated]


def export_has_duplicates(spec, store):
    r = engine.run(spec, store)
    if any(s.inflated for s in r.steps):
        spec, _ = engine.apply_fixes(spec, r)
        r = engine.run(spec, store)
    return not exports.cohort_frame(r)["sample_id"].is_unique


def route_spec(parser, request, store, args):
    if parser == "rules":
        return parse_request(request, store)
    from cohort_studio.llm import AIUnavailable, ai_parse
    try:
        spec, _ = ai_parse(request, store, provider=args.provider, model=args.model, base_url=args.base_url)
        return spec
    except AIUnavailable as exc:
        return {"request": request, "steps": [], "error": str(exc)}


def outcome(control, gold_infl, infl, exact):
    if control:
        return "false alarm" if infl else ("clean" if exact else "wrong cohort")
    if exact:
        return "shown + fixed" if infl else "avoided"
    return "wrong cohort" if infl else "missed"


def freeze(cases, store):
    for c in cases:
        spec = {"request": c["request"], "steps": c["gold_steps"]}
        ids, err = build_cohort(spec, store)
        c["expect"] = {"inflated_steps": inflated_steps(spec, store), "samples_after_fix": len(ids)}
        print(f"{c['id']:<32} inflates at {[s['step'] for s in c['expect']['inflated_steps']] or '-'}  "
              f"-> {len(ids)} samples after the fix")
    data = json.loads(CASES.read_text())
    data["cases"] = cases
    CASES.write_text(json.dumps(data, indent=1))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--parser", nargs="+", choices=["rules", "ai"], default=["rules"])
    ap.add_argument("--provider", choices=["openai", "anthropic"])
    ap.add_argument("--model")
    ap.add_argument("--base-url")
    ap.add_argument("--env-file", help="read API keys from this file into this process only (never printed)")
    ap.add_argument("--data-dir")
    ap.add_argument("--freeze", action="store_true")
    args = ap.parse_args(argv)
    if args.env_file:
        for line in pathlib.Path(args.env_file).read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.replace("export ", "").strip(), v.strip().strip('"').strip("'"))
    store = DataStore(args.data_dir)
    if not store.available("clinical"):
        print("Clinical/survival files are missing: run python data/tcga-brca/fetch.py --no-rna")
        return 2
    cases = json.loads(CASES.read_text())["cases"]
    if args.freeze:
        return freeze(cases, store)

    results = {}
    for parser in args.parser:
        rows = []
        for c in cases:
            gold_spec = {"request": c["request"], "steps": c["gold_steps"]}
            gold_ids, _ = build_cohort(gold_spec, store)
            spec = route_spec(parser, c["request"], store, args)
            infl = inflated_steps(spec, store) if spec.get("steps") else []
            ids, err = build_cohort(spec, store) if spec.get("steps") else ([], spec.get("error", "no steps"))
            exact = set(ids) == set(gold_ids)
            res = outcome(c["kind"] == "control", c.get("expect", {}).get("inflated_steps"), infl, exact)
            dup = bool(spec.get("steps")) and export_has_duplicates(spec, store)
            rows.append({"id": c["id"], "kind": c["kind"], "outcome": res, "exact": exact, "n": len(ids), "n_gold": len(gold_ids),
                         "inflated": infl, "duplicates_in_export": dup, "error": err, "steps": spec.get("steps")})
        results[parser] = rows

    print(f"{'case':<32}{'kind':<26}" + "".join(f"{p:<34}" for p in results))
    for i, c in enumerate(cases):
        cells = []
        for p in results:
            r = results[p][i]
            where = ",".join(f"S{s['step']}" for s in r["inflated"]) or "-"
            cells.append(f"{r['outcome']:<15} {r['n']:>5}/{r['n_gold']:<5} flag {where:<6}" + (" DUP!" if r["duplicates_in_export"] else ""))
        print(f"{c['id']:<32}{c['kind']:<26}" + "".join(f"{x:<34}" for x in cells))
    print()
    for p, rows in results.items():
        n_inf = [r for r in rows if r["kind"] != "control"]
        ok = sum(r["outcome"] in ("shown + fixed", "avoided") for r in n_inf)
        shown = sum(r["outcome"] == "shown + fixed" for r in n_inf)
        ctl = [r for r in rows if r["kind"] == "control"]
        print(f"{p}: right export {ok}/{len(n_inf)} inflating requests ({shown} showed the inflation, like the demo); "
              f"controls clean {sum(r['outcome'] == 'clean' for r in ctl)}/{len(ctl)}; "
              f"duplicate IDs in any export: {sum(r['duplicates_in_export'] for r in rows)}")
    out = EVALS / "results"
    out.mkdir(exist_ok=True)
    (out / "inflation.json").write_text(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
