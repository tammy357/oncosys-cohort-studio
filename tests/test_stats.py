import numpy as np
import pandas as pd

from cohort_studio.stats import permutation_shift_test


def _labels(n=1000, seed=1):
    rng = np.random.default_rng(seed)
    idx = [f"s{i}" for i in range(n)]
    return pd.Series(rng.choice(["Positive", "Negative"], n, p=[0.75, 0.25]), index=idx)


def test_random_subset_is_not_unusual():
    before = _labels()
    after = before.sample(600, random_state=2)
    res = permutation_shift_test(before, after)
    assert res["p_value"] > 0.05


def test_biased_subset_is_unusual():
    before = _labels()
    negatives = before[before == "Negative"].index[:150]
    after = before.drop(negatives)
    res = permutation_shift_test(before, after)
    assert res["tvd"] > res["random_tvd_95"]
    assert res["p_value"] < 0.01


def test_same_seed_gives_same_p_value():
    before = _labels()
    after = before.sample(600, random_state=3)
    assert permutation_shift_test(before, after) == permutation_shift_test(before, after)


def test_not_applicable_cases():
    before = _labels(100)
    assert permutation_shift_test(before, before) is None          # nothing removed
    assert permutation_shift_test(before, before.iloc[:0]) is None  # nothing kept
    new = pd.Series(["Positive"], index=["not-in-before"])
    assert permutation_shift_test(before, pd.concat([before.iloc[:50], new])) is None  # a join added samples


def test_several_columns_match_one_at_a_time():
    from cohort_studio.stats import permutation_shift_tests
    before = pd.DataFrame({"er": _labels(), "site": _labels(seed=5).map({"Positive": "A", "Negative": "B"})})
    after_ids = list(before.sample(600, random_state=4).index)
    together = permutation_shift_tests(before, after_ids, ["er", "site"])
    for col in ["er", "site"]:
        assert together[col] == permutation_shift_test(before[col], before.loc[after_ids, col])
