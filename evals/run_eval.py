"""Score the request parsers against the frozen answer key (evals/expected/*.csv).

    python evals/run_eval.py                                  # built-in rule parser, every case
    python evals/run_eval.py --parser ai                      # AI parser (provider from the environment)
    python evals/run_eval.py --parser rules ai --split dev    # both, tuning split only
    python evals/run_eval.py --parser ai --provider openai --base-url http://localhost:11434/v1 --model llama3.1
    python evals/run_eval.py --case tnbc_rna --case male -v   # a few cases, with step diffs

A case passes when the sample IDs the tool would export (run, fix inflating joins, run again, exactly
like `python -m cohort_studio`) equal the answer key: the same test the organizers' checker applies.

The AI parser is cohort_studio.llm.ai_parse, the route app.py and the CLI use: the grounded assistant in
cohort_studio/assistant/, taking each clarifying question's recommended (first) answer.

For the AI parser two numbers are reported:
  ai (raw)      the AI draft alone; a draft that can't run counts as a miss
  ai (shipped)  what the app does: fall back to the built-in parser when the AI draft can't run
Results go to evals/results/<timestamp>-<parser>.json (git-ignored).
"""
import argparse
import datetime
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from harness import EVALS, build_cohort, load_cases, read_expected, score

from cohort_studio.data import DataStore
from cohort_studio.parse import parse_request


def run_rules(case, store):
    spec = parse_request(case["request"], store)
    ids, error = build_cohort(spec, store)
    return {"spec_steps": spec["steps"], "ids": ids, "error": error, "unparsed": spec.get("unparsed", [])}


def run_ai(case, store, args):
    from cohort_studio.llm import AIUnavailable, ai_parse
    t0 = time.time()
    out: dict = {"fallback": False}
    try:
        spec, problems = ai_parse(case["request"], store, provider=args.provider, api_key=None, model=args.model,
                                  base_url=args.base_url)
        out.update(spec_steps=spec["steps"], unparsed=spec.get("unparsed", []), notes=spec.get("notes", []))
        if problems:
            out.update(ids=[], error="; ".join(m for _, m in problems), fallback=True)
        else:
            out["ids"], out["error"] = build_cohort(spec, store)
    except AIUnavailable as exc:
        out.update(spec_steps=None, ids=[], error=str(exc), fallback=True)
    except Exception as exc:   # a crash in the AI route is a miss for that case, not the end of the run
        out.update(spec_steps=None, ids=[], error=f"AI route crashed: {type(exc).__name__}: {exc}", fallback=True)
    out["seconds"] = round(time.time() - t0, 1)
    return out


def evaluate(parser, cases, store, args):
    rows = []
    if parser == "rules":
        results = [run_rules(c, store) for c in cases]
    else:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            results = list(pool.map(lambda c: run_ai(c, store, args), cases))
    for case, res in zip(cases, results):
        expected = read_expected(case["id"])
        row = {"id": case["id"], "split": case.get("split", "dev"), "tags": case.get("tags", []),
               "request": case["request"], **res, **score(res["ids"], expected)}
        if parser == "ai":
            shipped_ids = res["ids"]
            if res["fallback"]:
                shipped_ids, _ = build_cohort(parse_request(case["request"], store), store)
            row["shipped_exact"] = set(shipped_ids) == set(expected)
        row["gold_steps"] = case["gold_steps"]
        row.pop("ids")
        rows.append(row)
    return rows


def summarize(parser, rows):
    lines = []
    for split in ("dev", "holdout", "all"):
        sub = [r for r in rows if split == "all" or r["split"] == split]
        if not sub:
            continue
        n = len(sub)
        exact = sum(r["exact"] for r in sub)
        jac = sum(r["jaccard"] for r in sub) / n
        line = f"  {split:<8} exact {exact:>2}/{n:<2} ({100 * exact / n:5.1f}%)   mean Jaccard {jac:.3f}"
        if parser == "ai":
            shipped = sum(r["shipped_exact"] for r in sub)
            fb = sum(r["fallback"] for r in sub)
            line += f"   shipped {shipped}/{n} ({100 * shipped / n:.1f}%)   fallbacks {fb}"
        lines.append(line)
    return "\n".join(lines)


def print_rows(parser, rows, verbose):
    print(f"\n== {parser} ==")
    print(f"{'case':<28} {'split':<8} {'ok':<4} {'pred':>5} {'gold':>5} {'miss':>5} {'extra':>5}  note")
    for r in rows:
        note = r["error"][:70] if r["error"] else ""
        if parser == "ai" and r["fallback"]:
            note = f"[fallback: {'ok' if r['shipped_exact'] else 'miss'}] " + note
        print(f"{r['id']:<28} {r['split']:<8} {'✓' if r['exact'] else '✗':<4} {r['n_pred']:>5} {r['n_gold']:>5} "
              f"{r['missing']:>5} {r['extra']:>5}  {note}")
        if verbose and not r["exact"]:
            print(f"    request: {r['request']}")
            gold = {json.dumps(s, sort_keys=True) for s in r["gold_steps"]}
            pred = {json.dumps(s, sort_keys=True) for s in (r["spec_steps"] or [])}
            for s in sorted(gold - pred):
                print(f"    - gold only: {s}")
            for s in sorted(pred - gold):
                print(f"    + parser only: {s}")
            if r.get("unparsed"):
                print(f"    unparsed: {r['unparsed']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--parser", nargs="+", choices=["rules", "ai"], default=["rules"])
    ap.add_argument("--split", choices=["dev", "holdout", "all"], default="all")
    ap.add_argument("--case", action="append", help="run only this case id (repeatable)")
    ap.add_argument("--provider", choices=["anthropic", "openai"],
                    help="default: COHORT_STUDIO_PROVIDER, else anthropic if only ANTHROPIC_API_KEY is set, else openai")
    ap.add_argument("--model", help="model name (default: COHORT_STUDIO_MODEL or the assistant's default)")
    ap.add_argument("--base-url", help="OpenAI-compatible endpoint, e.g. a local Ollama server")
    ap.add_argument("--jobs", type=int, default=4, help="parallel AI requests")
    ap.add_argument("--min-exact", type=float, help="exit 1 if exact-match %% (all selected cases) is below this")
    ap.add_argument("--data-dir")
    ap.add_argument("-v", "--verbose", action="store_true", help="show step differences for misses")
    args = ap.parse_args(argv)
    if "ai" in args.parser:
        # the key from the repository's .env, as the command line and the app read it (never printed)
        from cohort_studio.env import load_dotenv
        load_dotenv(EVALS.parent / ".env")

    store = DataStore(args.data_dir)
    if not store.available("clinical"):
        print("Clinical/survival/mutation files are missing: run python data/tcga-brca/fetch.py --no-rna")
        return 2
    cases = load_cases(args.split, args.case)
    missing = [c["id"] for c in cases if read_expected(c["id"]) is None]
    if missing:
        print(f"No answer key for {missing}: run python evals/build_expected.py")
        return 2

    status = 0
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    (EVALS / "results").mkdir(exist_ok=True)
    summaries = []
    for parser in args.parser:
        rows = evaluate(parser, cases, store, args)
        print_rows(parser, rows, args.verbose)
        summaries.append(f"{parser}{' (' + (args.model or 'default model') + ')' if parser == 'ai' else ''}:\n"
                         + summarize(parser, rows))
        out = EVALS / "results" / f"{stamp}-{parser}.json"
        meta = {"parser": parser, "provider": args.provider if parser == "ai" else None,
                "model": args.model if parser == "ai" else None, "base_url": args.base_url if parser == "ai" else None,
                "split": args.split}
        out.write_text(json.dumps({"meta": meta, "rows": rows}, indent=1))
        pct = 100 * sum(r["exact"] for r in rows) / len(rows)
        if args.min_exact is not None and pct < args.min_exact:
            status = 1
    print("\n" + "\n".join(summaries))
    print(f"\nDetails: evals/results/{stamp}-*.json")
    return status


if __name__ == "__main__":
    sys.exit(main())
