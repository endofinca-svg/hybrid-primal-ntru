from math import exp, log, lgamma, floor

# ============================================================
# Pattern probability helpers
# ============================================================

def log_binom(n, k):
    """
    Natural logarithm of binomial coefficient C(n, k).
    """
    if k < 0 or k > n:
        return float("-inf")

    return lgamma(n + 1) - lgamma(k + 1) - lgamma(n - k + 1)


def single_hit_prob(n, d, k_pat):
    """
    Fixed-pattern success probability:

        p_k = C(n-k, d) / C(n, d)

    Here:
      n     : ring dimension
      d     : Hamming weight of the sparse secret
      k_pat : pattern length
    """
    if not (0 <= k_pat <= n):
        return 0.0

    if not (0 <= d <= n):
        return 0.0

    if d > n - k_pat:
        return 0.0

    lp = log_binom(n - k_pat, d) - log_binom(n, d)

    return exp(lp)


def overall_pattern_success_prob(n, d, k_pat):
    """
    Rotation-amplified pattern success probability:

        P_pat(k) ≈ 1 - (1 - p_k)^n

    This corresponds to trying all cyclic rotations heuristically.
    """
    pk = single_hit_prob(n, d, k_pat)

    if pk <= 0.0:
        return 0.0

    if pk >= 1.0:
        return 1.0

    return 1.0 - exp(n * log(1.0 - pk))


def pattern_penalty_bits(n, d, k_pat):
    """
    Pattern amplification penalty:

        penalty_pat = -log2(P_pat)
    """
    P = overall_pattern_success_prob(n, d, k_pat)

    if P <= 0.0:
        return float("inf")

    return -log(P, 2)


def pigeonhole_start_k(n, h):
    """
    Nontrivial search start:

        floor((n-1)/h) + 1

    This avoids starting from very small pattern lengths that are usually trivial.
    """
    if h <= 0:
        return 1

    return max(1, floor((n - 1) / h) + 1)