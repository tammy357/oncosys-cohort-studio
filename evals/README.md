# Parser benchmark

Measures how often a plain-English request turns into **exactly the right `cohort_samples.csv`**, which is
the test the organizers' checker applies to the hidden request.

```bash
python data/tcga-brca/fetch.py --no-rna                       # once: the clinical/survival/mutation files
python evals/build_expected.py --check                        # answer key still reproduces? (CI-friendly)
python evals/run_eval.py -v                                   # built-in rule parser, with step diffs for misses
ANTHROPIC_API_KEY=... python evals/run_eval.py --parser rules ai --provider anthropic --split holdout
python evals/run_eval.py --parser ai --provider openai \
    --base-url http://localhost:11434/v1 --model qwen2.5:7b --jobs 1   # local model, no key
```

## How a case is scored

1. The parser turns `request` into a spec.
2. The spec goes through the same path as `python -m cohort_studio`: run the engine, apply the inflation
   fix if any join inflated, run again, take the sample IDs.
3. **Pass** = that set of sample IDs equals `expected/<id>.csv`. Jaccard, missing and extra counts show how
   far off a miss is.

The AI parser is `cohort_studio.llm.ai_parse`, the same route `app.py` and the CLI use: Hriday's grounded assistant
(`cohort_studio/assistant/`), taking each clarifying question's recommended (first) answer, since the checker can't
answer questions. `exact` is the AI draft alone, and `shipped` is what the app actually exports (the built-in parser
takes over when the AI draft can't run).

## Files

| file | what it is |
|---|---|
| `cases.json` | requests + gold steps + `why` (the judgement call) + `split` + `tags` |
| `expected/*.csv` | the frozen answer key, written by `build_expected.py` from the gold steps |
| `harness.py` | loading cases, the CLI-equivalent `build_cohort`, scoring |
| `run_eval.py` | the benchmark; results go to `results/` (git-ignored) |

## Rules that keep the number honest

- **Tune on `dev`, report `holdout`.** The 10 `p_*` cases are paraphrases of other cases with the same answer:
  they test whether a parser follows the meaning or just the wording.
- **Never copy a benchmark request into the prompt examples** in `cohort_studio/assistant/prompt.py`.
  `tests/test_evals.py` fails if you do.
- **The gold steps are a judgement.** If you disagree with one, change its `gold_steps` and `why`, then run
  `build_expected.py` and commit the changed CSV in the same commit.
- The cases were written by someone who had read `parse.py`, so the rule parser's score on the original
  cases flatters it. The paraphrases are the better guide to an unseen request.

## Adding a case

Add an entry to `cases.json` (or `"gold_from": "<id>"` for a paraphrase), run `python evals/build_expected.py`,
check the printed sample and patient counts make sense, and commit the new CSV. A gold spec that fails to run,
or selects nobody, is reported as a problem.
