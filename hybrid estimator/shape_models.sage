from math import pi, exp, log, sqrt, floor
from sage.all import infinity

# ============================================================
# Shape model helpers
# We now use direct code for GSA and ZGSA.
# No lattice-estimator simulator is called here.
# CN11 is removed.
# ============================================================

SHAPE_MODELS = {
    "gsa": "gsa",
    "zgsa": "zgsa",
}


def normalize_shape_model(s):
    """
    Normalize shape model name.

    Supported:
      - gsa
      - zgsa

    """
    s = s.strip().lower()

    if s not in SHAPE_MODELS:
        raise ValueError(f"Unknown shape model: {s}. Supported: gsa, zgsa")

    return s


# ============================================================
# Volume, Gaussian heuristic, and scaling helpers
# ============================================================

def scaled_logvol(d, n_id, q, xi):
    """
    log(vol) = (d - n_id) log(q) + n_id log(xi)

    Here:
      d - n_id : q-block dimension
      n_id     : identity/scaled block dimension
    """
    return (d - n_id) * log(q) + n_id * log(xi)


def GH_sv_factor_squared(k):
    """
    Squared Gaussian heuristic factor.
    """
    return ((pi * k)**(1. / k) * k / (2. * pi * exp(1)))


def gh_lambda1(d, logvol):
    """
    Gaussian heuristic estimate of lambda_1.
    """
    if d <= 0:
        return infinity

    return sqrt(GH_sv_factor_squared(d)) * exp(logvol / d)


def vector_typical_norm(dim, stddev):
    """
    Typical norm of a vector with independent coefficients of standard deviation stddev.
    """
    if dim <= 0:
        return 0.0

    return sqrt(dim) * stddev


def xi_factor(norm_s, norm_e):
    """
    Scale secret side so that xi * ||s|| ≈ ||e|| when the secret side is shorter.
    """
    if norm_s <= 0:
        return 1.0

    if norm_s < norm_e:
        return norm_e / norm_s

    return 1.0


# ============================================================
# GSA delta
# This follows the code you gave:
# small table for beta < 50, Gaussian heuristic formula after that.
# ============================================================

def compute_delta(k):
    """
    Computes delta from the BKZ block size k.

    For small k, use the standard interpolation table.
    For k >= 50, use the Gaussian heuristic formula:

        delta = GH_sv_factor_squared(k)^(1/(2k-2)).
    """

    small = {
        0: 1e20,
        1: 1e20,
        2: 1.021900,
        3: 1.020807,
        4: 1.019713,
        5: 1.018620,
        6: 1.018128,
        7: 1.017636,
        8: 1.017144,
        9: 1.016652,
        10: 1.016160,
        11: 1.015898,
        12: 1.015636,
        13: 1.015374,
        14: 1.015112,
        15: 1.014850,
        16: 1.014720,
        17: 1.014590,
        18: 1.014460,
        19: 1.014330,
        20: 1.014200,
        21: 1.014044,
        22: 1.013888,
        23: 1.013732,
        24: 1.013576,
        25: 1.013420,
        26: 1.013383,
        27: 1.013347,
        28: 1.013310,
        29: 1.013253,
        30: 1.013197,
        31: 1.013140,
        32: 1.013084,
        33: 1.013027,
        34: 1.012970,
        35: 1.012914,
        36: 1.012857,
        37: 1.012801,
        38: 1.012744,
        39: 1.012687,
        40: 1.012631,
        41: 1.012574,
        42: 1.012518,
        43: 1.012461,
        44: 1.012404,
        45: 1.012348,
        46: 1.012291,
        47: 1.012235,
        48: 1.012178,
        49: 1.012121,
        50: 1.012065,
    }

    # Linear interpolation for non-integer beta.
    if k != round(k):
        x = k - floor(k)
        d1 = compute_delta(floor(k))
        d2 = compute_delta(floor(k) + 1)
        return x * d2 + (1 - x) * d1

    k = int(k)

    if k < 50:
        return small[k]
    else:
        return GH_sv_factor_squared(k)**(1. / (2. * k - 2.))


# ============================================================
# GSA profile
# ============================================================

def bkzgsa_gso_len(logvol, i, d, beta=None, delta=None):
    """
    GSA Gram-Schmidt length at index i.

        ||b_i^*|| = delta^(d - 1 - 2i) * exp(logvol / d)

    where logvol is the natural logarithm of the lattice volume.
    """
    if delta is None:
        delta = compute_delta(beta)

    return delta**(d - 1 - 2 * i) * exp(logvol / d)


def gsa_profile(d, n_id, q, beta, xi):
    """
    Return the full GSA profile as ordinary lengths, not logs.
    """
    logvol = scaled_logvol(d, n_id, q, xi)

    delta = compute_delta(beta)

    return [
        bkzgsa_gso_len(logvol, i, d, delta=delta)
        for i in range(d)
    ]


def gsa_profile_len_at_index(i, d, n_id, q, beta, xi):
    """
    Return the i-th GSA GSO length.
    """
    logvol = scaled_logvol(d, n_id, q, xi)

    return bkzgsa_gso_len(logvol, i, d, beta=beta)


# ============================================================
# ZGSA delta
# Slightly adapted from the ZGSA code you provided.
# ============================================================

def delta_0f(k):
    """
    Auxiliary function giving root Hermite factors.

    Small values are experimentally determined.
    For larger k, use the asymptotic formula.
    """
    small = (
        (2, 1.02190),
        (5, 1.01862),
        (10, 1.01616),
        (15, 1.01485),
        (20, 1.01420),
        (25, 1.01342),
        (28, 1.01331),
        (40, 1.01295),
    )

    k = float(k)

    if k <= 2:
        return 1.0219

    elif k < 40:
        for i in range(1, len(small)):
            if small[i][0] > k:
                return small[i - 1][1]

    elif k == 40:
        return small[-1][1]

    else:
        return (
            k / (2 * pi * exp(1)) * (pi * k)**(1. / k)
        )**(1 / (2 * (k - 1.)))


# ============================================================
# ZGSA profile
# Generalized from [q,...,q,1,...,1] to [q,...,q,xi,...,xi].
# ============================================================

def construct_BKZ_shape_scaled(q, nq, n1, beta, xi=1.0):
    """
    Simulates the log-shape of a basis after BKZ-beta reduction.

    Original ZGSA model:
        [q, ..., q, 1, ..., 1]

    Here we use the scaled version:
        [q, ..., q, xi, ..., xi]

    Parameters:
      q    : modulus
      nq   : number of q-vectors
      n1   : number of identity/scaled vectors
      beta : BKZ block size
      xi   : scaling factor on the identity block

    Returns:
      a, a + B, L

    where L is a list of natural-log GSO lengths.
    """
    d = nq + n1

    if d <= 0:
        return 0, 0, []

    lq = log(q)
    lx = log(xi)

    # Goal log-volume.
    glv = nq * lq + n1 * lx

    # beta = 0 means unreduced shape.
    if beta == 0:
        L = nq * [lq] + n1 * [lx]
        return nq, nq, L

    slope = -2 * log(delta_0f(beta))

    # If the slope is numerically invalid, fall back to the unreduced shape.
    if slope >= 0:
        L = nq * [lq] + n1 * [lx]
        return nq, nq, L

    # Length of the sloped region between log(q) and log(xi).
    height = lq - lx

    if height <= 0:
        # This should not happen in our parameter regime because q >> xi.
        L = d * [glv / d]
        return 0, d, L

    B = int(floor(height / (-slope)))

    if B <= 0:
        L = nq * [lq] + n1 * [lx]
        return nq, nq, L

    # Long profile from which we slide a window of length d.
    #
    # Original:
    #   nq*[log(q)] + [log(q)+i*slope] + n1*[0]
    #
    # Scaled:
    #   nq*[log(q)] + [log(q)+i*slope] + n1*[log(xi)]
    #
    L_long = nq * [lq] + [lq + i * slope for i in range(1, B + 1)] + n1 * [lx]

    x = 0
    lv = sum(L_long[:d])

    # Slide window to the right while current volume exceeds target volume.
    while lv > glv and x + d < len(L_long):
        lv -= L_long[x]
        lv += L_long[x + d]
        x += 1

    L = L_long[x:x + d]

    # Number of q-vectors remaining at the beginning.
    a = max(0, nq - x)

    # Length of the sloped region inside the selected window.
    B_eff = min(B, d - a)

    # Volume correction on the sloped region.
    diff = glv - lv

    if B_eff > 0:
        for i in range(a, a + B_eff):
            L[i] += diff / B_eff
    else:
        # If there is no sloped region, distribute volume correction uniformly.
        corr = diff / d
        L = [x + corr for x in L]

    return a, a + B_eff, L


def zgsa_log_profile(d, n_id, q, beta, xi):
    """
    Return ZGSA profile as natural-log GSO lengths.

    Here:
      nq = d - n_id
      n1 = n_id
    """
    nq = d - n_id
    n1 = n_id

    _, _, L = construct_BKZ_shape_scaled(
        q=q,
        nq=nq,
        n1=n1,
        beta=beta,
        xi=xi
    )

    return L


def zgsa_profile(d, n_id, q, beta, xi):
    """
    Return the full ZGSA profile as ordinary lengths.
    """
    L = zgsa_log_profile(d, n_id, q, beta, xi)

    return [exp(x) for x in L]


def zgsa_profile_len_at_index(i, d, n_id, q, beta, xi):
    """
    Return the i-th ZGSA GSO length.
    """
    L = zgsa_log_profile(d, n_id, q, beta, xi)

    return exp(L[i])


# ============================================================
# Unified profile interface
# ============================================================

PROFILE_CACHE = {}


def _cache_key(shape_model, d, n_id, q, beta, xi):
    """
    Cache key for GSA/ZGSA profiles.
    """
    return (
        normalize_shape_model(shape_model),
        int(d),
        int(n_id),
        int(q),
        int(beta),
        float(xi),
    )


def simulate_profile(shape_model, d, n_id, q, beta, xi):
    """
    Return a profile:

        [||b_0^*||, ..., ||b_{d-1}^*||]

    using direct GSA/ZGSA code.
    """
    shape_model = normalize_shape_model(shape_model)

    key = _cache_key(shape_model, d, n_id, q, beta, xi)

    if key in PROFILE_CACHE:
        return PROFILE_CACHE[key]

    if shape_model == "gsa":
        prof = gsa_profile(d, n_id, q, beta, xi)

    elif shape_model == "zgsa":
        prof = zgsa_profile(d, n_id, q, beta, xi)

    else:
        raise ValueError(f"Unsupported shape model: {shape_model}")

    PROFILE_CACHE[key] = prof

    return prof


def profile_len_at_index(i, d, beta, shape_model, n_id, q, xi):
    """
    Return the i-th GSO length from the selected shape model.

    For speed, GSA is computed directly at index i.
    ZGSA constructs the log-shape and returns index i.
    """
    shape_model = normalize_shape_model(shape_model)

    if i < 0 or i >= d:
        raise IndexError(f"GSO index i={i} out of range for d={d}")

    if shape_model == "gsa":
        return gsa_profile_len_at_index(i, d, n_id, q, beta, xi)

    elif shape_model == "zgsa":
        return zgsa_profile_len_at_index(i, d, n_id, q, beta, xi)

    else:
        raise ValueError(f"Unsupported shape model: {shape_model}")


# ============================================================
# Beta search
# ============================================================

def find_beta_scaled(d, n_id, q, xi, target_norm, shape_model="gsa"):
    """
    Find the smallest beta satisfying the current BDD success test.

    We keep the same success condition as the previous code:

        target_sigma = ||v|| / sqrt(d)

    Then for each beta, check the GSO length at index d - beta:

        0.5 log(beta) + log(target_sigma) < log(||b^*_{d-beta}||)

    Supported shape models:
      - gsa
      - zgsa
    """
    shape_model = normalize_shape_model(shape_model)

    if d <= 0 or n_id < 0 or n_id > d:
        return infinity

    if target_norm <= 0:
        return infinity

    target_sigma = target_norm / sqrt(d)

    for beta in range(100, d // 2 + 1):
        i = d - beta

        gso_len = profile_len_at_index(
            i=i,
            d=d,
            beta=beta,
            shape_model=shape_model,
            n_id=n_id,
            q=q,
            xi=xi
        )

        lhs = 0.5 * log(beta) + log(target_sigma)

        if lhs < log(gso_len):
            return beta

    return infinity