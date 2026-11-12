"""Is a composition shift bigger than random dropout of the same size would cause?

For one characteristic (for example ER status), a step keeps n of the N samples it received.
The observed shift is the total variation distance (TVD) between the mix before and after.
The permutation test draws n of the N samples at random, many times, and records the TVD each
random draw produces. The p-value is the share of random draws that move the mix at least as
much as the real step did.

Random dropout is not zero-shift: with few samples or many categories (such as submitting site),
a random draw can move the mix noticeably. The test accounts for that, which a fixed TVD
threshold on its own does not.

All characteristics of one step share the same random draws, so testing six characteristics
costs little more than testing one.
"""
import numpy as np
import pandas as pd

N_PERMUTATIONS = 1000
SEED = 0  # fixed, so the same cohort always gets the same p-value


def _random_picks(n_before, n_after, n_permutations, seed):
    """n_permutations random subsets of size n_after from range(n_before), one per row."""
    rng = np.random.default_rng(seed)
    keys = rng.random((n_permutations, n_before))
    return np.argpartition(keys, n_after - 1, axis=1)[:, :n_after]


def _test_codes(codes, after_mask, picks, n_permutations):
    k = int(codes.max()) + 1
    n_before, n_after = len(codes), int(after_mask.sum())
    base = np.bincount(codes, minlength=k) / n_before
    observed = float(0.5 * np.abs(np.bincount(codes[after_mask], minlength=k) / n_after - base).sum())
    flat = (codes[picks] + np.arange(n_permutations)[:, None] * k).ravel()
    counts = np.bincount(flat, minlength=n_permutations * k).reshape(n_permutations, k)
    null = 0.5 * np.abs(counts / n_after - base).sum(axis=1)
    # +1 on both sides so the p-value is never exactly 0 (standard for permutation tests)
    p_value = (1 + int(np.sum(null >= observed - 1e-12))) / (1 + n_permutations)
    return {
        "tvd": round(observed, 3),
        "p_value": round(p_value, 4),
        "random_tvd_median": round(float(np.median(null)), 3),
        "random_tvd_95": round(float(np.quantile(null, 0.95)), 3),
        "n_before": n_before,
        "n_after": n_after,
        "n_permutations": n_permutations,
    }


def permutation_shift_tests(before, after_ids, columns, n_permutations=N_PERMUTATIONS, seed=SEED):
    """Test several characteristics of one step at once.

    before: DataFrame indexed by sample_id (one row per sample the step received).
    after_ids: the sample_ids the step kept.
    Returns {column: result dict}. Columns are skipped when the test doesn't apply: nothing removed,
    the step added samples that weren't in its input (as a join of matched normals does), or a column
    has missing values (then use permutation_shift_test on that column alone).
    """
    before = before.dropna(how="all")
    after_mask = before.index.isin(list(after_ids))
    n_before, n_after = len(before), int(after_mask.sum())
    if n_after == 0 or n_after >= n_before or n_after < len(set(after_ids)):
        return {}
    picks = _random_picks(n_before, n_after, n_permutations, seed)
    out = {}
    for col in columns:
        values = before[col]
        if values.isna().any():
            continue
        codes, _ = pd.factorize(values, sort=True)
        out[col] = _test_codes(codes, after_mask, picks, n_permutations)
    return out


def permutation_shift_test(before, after, n_permutations=N_PERMUTATIONS, seed=SEED):
    """Compare the shift from `before` to `after` for one characteristic.

    before, after: pandas Series of category labels, indexed by sample_id.
    Returns a result dict, or None when the test doesn't apply (nothing removed, or `after` has
    samples not in `before`, as when a join adds matched normal samples).
    """
    before = before.dropna()
    after = after.dropna()
    if len(after) == 0 or len(after) >= len(before) or not after.index.isin(before.index).all():
        return None
    codes, _ = pd.factorize(before, sort=True)
    picks = _random_picks(len(before), len(after), n_permutations, seed)
    return _test_codes(codes, before.index.isin(after.index), picks, n_permutations)
