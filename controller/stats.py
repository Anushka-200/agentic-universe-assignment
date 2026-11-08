"""Deterministic statistics used to decide winners. Standard library only.

two_prop_z  : one-sided test "is rate B really higher than rate A?" -> p-value
holm        : Holm-Bonferroni correction when we test several arms at once
"""
import math


def two_prop_z(succ_a, n_a, succ_b, n_b):
    """One-sided p-value for H1: p_b > p_a, using the pooled two-proportion z-test.

    A small p means "B beating A by this much would be very unlikely if they were truly equal".
    """
    if n_a == 0 or n_b == 0:
        return 1.0
    pa, pb = succ_a / n_a, succ_b / n_b
    pool = (succ_a + succ_b) / (n_a + n_b)
    se = math.sqrt(pool * (1 - pool) * (1 / n_a + 1 / n_b))
    if se == 0:
        return 1.0
    z = (pb - pa) / se
    return 0.5 * math.erfc(z / math.sqrt(2))  # P(Z >= z)


def holm(pvalues, alpha):
    """Holm step-down. pvalues: {key: p}. Returns the set of keys that stay significant.

    Sort p ascending; the i-th smallest must be <= alpha / (m - i). Stop at the first failure.
    Controls the chance of ANY false winner at `alpha`, but is less strict than plain Bonferroni.
    """
    m = len(pvalues)
    passed = set()
    for i, (k, p) in enumerate(sorted(pvalues.items(), key=lambda kv: (kv[1], kv[0]))):
        if p > alpha / (m - i):
            break
        passed.add(k)
    return passed


def tv_distance(a, b):
    """Total-variation distance between two allocation dicts (weights are normalised first)."""
    def norm(d):
        s = sum(d.values()) or 1.0
        return {k: v / s for k, v in d.items()}
    a, b = norm(a), norm(b)
    return 0.5 * sum(abs(a.get(k, 0) - b.get(k, 0)) for k in set(a) | set(b))
