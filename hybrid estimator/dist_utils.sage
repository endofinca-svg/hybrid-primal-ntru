from math import log, ceil

# ============================================================
# Basic numerical helpers
# ============================================================

def entropy(D):
    """
    Shannon entropy of a discrete distribution D.
    D is a dictionary: value -> probability.
    """
    H = 0.0
    for p in D.values():
        if p > 0:
            H += -p * log(p, 2)
    return H


def log2_add(a, b):
    """
    Compute log2(2^a + 2^b) stably.
    """
    m = max(a, b)
    return m + log(1 + 2**(-abs(a - b)), 2)


def plot_gso(r, *args, **kwds):
    """
    Plot a GSO profile.
    """
    return line([(i, r_,) for i, r_ in enumerate(r)], *args, **kwds)


# ============================================================
# Distribution helpers
# ============================================================

def build_law(dist_name, n, param):
    """
    Build the coefficient distribution law.

    Supported:
      - ternary
      - binomial
      - gaussian

    Returns:
      stddev, D
    """
    dist_name = dist_name.strip().lower()

    if dist_name == "ternary":
        return build_ternary_law(n, int(param))

    elif dist_name == "binomial":
        return build_centered_binomial_law(int(param))

    elif dist_name == "gaussian":
        return build_Gaussian_law(param, ceil(3 * param))

    else:
        raise ValueError(f"Unsupported distribution: {dist_name}")
    
def effective_hamming_weight(n, D):
    """
    Density-to-fixed-weight approximation:
        h_eff = round(n * Pr[X != 0]).
    """
    p_nonzero = 1.0 - float(D.get(0, 0.0))
    return int(round(n * p_nonzero))
    
def residual_secret_stats(s_dist, n_id, n_pat, pattern_hw, std_s):
    """
    Return (norm_f_res, sigma_f_res_eff).

    For ternary, param_s is interpreted as a fixed Hamming weight.
    For binomial and gaussian, std_s is the per-coordinate standard deviation
    returned by build_law().
    """
    if n_id <= 0 or n_pat <= 0:
        return None, None

    s_dist = s_dist.strip().lower()

    if s_dist == "ternary":
        # Fixed-weight ternary.
        #
        # pattern_hw is the estimated Hamming weight after pattern reduction.
        # Tail guessing keeps n_id coordinates out of n_pat, hence:
        #
        #     h_res ≈ pattern_hw * n_id / n_pat.
        #
        h_res = int(ceil(pattern_hw * n_id / n_pat))
        h_res = min(h_res, n_id)

        if h_res <= 0:
            return None, None

        norm_f_res = sqrt(h_res)
        sigma_f_res_eff = norm_f_res / sqrt(n_id)
        return norm_f_res, sigma_f_res_eff

    else:
        # Binomial / Gaussian.
        #
        # std_s is already the per-coordinate standard deviation returned
        # by build_law(). Tail guessing is modeled as keeping n_id coordinates
        # with the same marginal coefficient law.
        #
        #     ||f_res|| ≈ sqrt(n_id) * std_s.
        #
        sigma_f_res_eff = std_s

        if sigma_f_res_eff <= 0:
            return None, None

        norm_f_res = sqrt(n_id) * sigma_f_res_eff
        return norm_f_res, sigma_f_res_eff