import argparse, sys, inspect
from math import pi, exp, log, sqrt, ceil, lgamma, floor
from sage.all import *

from batchCVP import batchCVPP_cost

sys.path.append("/home/inca/lattice-estimator")
from estimator.reduction import RC
import estimator.simulator as Simulator

# loading sage files

load("proba_utils.sage")

sys.stdout.flush()

# ============================================================
# Basic helpers
# ============================================================

def entropy(D):
    H = 0.0
    for p in D.values():
        if p > 0:
            H += -p * log(p, 2)
    return H


def log2_add(a, b):
    m = max(a, b)
    return m + log(1 + 2**(-abs(a - b)), 2)


def plot_gso(r, *args, **kwds):
    return line([(i, r_,) for i, r_ in enumerate(r)], *args, **kwds)


# ============================================================
# Distribution helpers
# ============================================================

def build_law(dist_name, n, param):
    dist_name = dist_name.strip().lower()

    if dist_name == "ternary":
        return build_ternary_law(n, int(param))
    elif dist_name == "binomial":
        return build_centered_binomial_law(int(param))
    elif dist_name == "gaussian":
        return build_Gaussian_law(param, ceil(3 * param))
    else:
        raise ValueError(f"Unsupported distribution: {dist_name}")


# ============================================================
# Cost model helpers (all through lattice-estimator RC)
# ============================================================

COST_MODELS = {
    "MATZOV": RC.MATZOV,
    "BDGL16": RC.BDGL16,
    "ABLR21": RC.ABLR21,
    "CheNgu12": RC.CheNgu12,
    "LaaMosPol14": RC.LaaMosPol14,
}


def normalize_cost_model(s):
    s = s.strip()
    for k in COST_MODELS:
        if s.lower() == k.lower():
            return k
    raise ValueError(f"Unknown cost model: {s}")


def bkz_cost(beta, d, alg="MATZOV"):
    beta = int(round(beta))
    alg = normalize_cost_model(alg)
    cfun = COST_MODELS[alg]
    c = cfun(beta, d)
    return log(c, 2)


# ============================================================
# Shape model helpers (all through lattice-estimator simulator)
# ============================================================

SHAPE_MODELS = {
    "gsa": Simulator.GSA,
    "zgsa": Simulator.ZGSA,
    "cn11": Simulator.CN11,
}


def normalize_shape_model(s):
    s = s.strip().lower()
    if s not in SHAPE_MODELS:
        raise ValueError(f"Unknown shape model: {s}")
    return s


def scaled_logvol(d, n_id, q, xi):
    """
    log(vol) = (d - n_id) log(q) + n_id log(xi)
    """
    return (d - n_id) * log(q) + n_id * log(xi)


def GH_sv_factor_squared(k):
    return ((pi * k)**(1. / k) * k / (2. * pi * exp(1)))


def gh_lambda1(d, logvol):
    if d <= 0:
        return infinity
    return sqrt(GH_sv_factor_squared(d)) * exp(logvol / d)


def _coerce_profile_to_norms(prof, d):
    """
    Normalize simulator output into a list of length d with positive numbers
    representing ||b_i^*|| approximately.
    """
    # sometimes profile is wrapped
    if isinstance(prof, tuple) and len(prof) > 0:
        # pick the first vector-like entry of length d if present
        for x in prof:
            try:
                xs = list(x)
                if len(xs) == d:
                    prof = xs
                    break
            except Exception:
                pass

    prof_list = list(prof)
    if len(prof_list) != d:
        raise ValueError(f"Simulator returned profile of length {len(prof_list)} but expected {d}")

    vals = [float(x) for x in prof_list]

    # if entries look like logs, exponentiate
    # heuristic: many non-positive values usually indicate log-profile
    nonpos = sum(1 for x in vals if x <= 0)
    if nonpos > d // 3:
        vals = [exp(x) for x in vals]

    return vals


def _try_qary_simulator_kwargs(d, n_id, q, beta, xi, shape_obj):
    """
    Try several likely call signatures for lattice-estimator's qary_simulator.

    We model the lattice as:
      - q-block dimension = d - n_id
      - identity/scaled block dimension = n_id
      - total dim d
      - scaled identity factor xi
    """
    attempts = []

    # common keyword variants
    attempts.append(dict(d=d, n=d - n_id, q=q, beta=beta, xi=xi, simulator=shape_obj))
    attempts.append(dict(d=d, n=d - n_id, q=q, beta=beta, xi=xi, red_shape_model=shape_obj))
    attempts.append(dict(d=d, n=d - n_id, q=q, beta=beta, simulator=shape_obj))
    attempts.append(dict(d=d, n=d - n_id, q=q, beta=beta, red_shape_model=shape_obj))
    attempts.append(dict(d=d, n=d - n_id, q=q, beta=beta, xi=xi, tau=False, dual=False, simulator=shape_obj))
    attempts.append(dict(d=d, n=d - n_id, q=q, beta=beta, xi=xi, tau=False, dual=False, red_shape_model=shape_obj))

    # some versions may use m instead of d or vice versa
    attempts.append(dict(m=d, n=d - n_id, q=q, beta=beta, xi=xi, simulator=shape_obj))
    attempts.append(dict(m=d, n=d - n_id, q=q, beta=beta, xi=xi, red_shape_model=shape_obj))

    return attempts


def simulate_profile_with_estimator(shape_model, d, n_id, q, beta, xi):
    """
    Return a profile [||b_0^*||, ..., ||b_{d-1}^*||] using lattice-estimator.
    """
    shape_model = normalize_shape_model(shape_model)
    shape_obj = SHAPE_MODELS[shape_model]

    last_err = None

    # First try qary_simulator with multiple candidate signatures.
    if hasattr(Simulator, "qary_simulator"):
        for kw in _try_qary_simulator_kwargs(d, n_id, q, beta, xi, shape_obj):
            try:
                prof = Simulator.qary_simulator(**kw)
                return _coerce_profile_to_norms(prof, d)
            except Exception as e:
                last_err = e

    # Next try direct shape object calls with several signatures.
    direct_attempts = [
        lambda: shape_obj(d=d, n=d - n_id, q=q, beta=beta, xi=xi),
        lambda: shape_obj(d=d, n=d - n_id, q=q, beta=beta),
        lambda: shape_obj(d, d - n_id, q, beta, xi),
        lambda: shape_obj(d, d - n_id, q, beta),
    ]

    for f in direct_attempts:
        try:
            prof = f()
            return _coerce_profile_to_norms(prof, d)
        except Exception as e:
            last_err = e

    raise RuntimeError(
        f"Could not obtain BKZ profile from lattice-estimator simulator for "
        f"shape_model={shape_model}. Last error: {last_err}"
    )


def profile_len_at_index(i, d, beta, shape_model, n_id, q, xi):
    prof = simulate_profile_with_estimator(
        shape_model=shape_model,
        d=d,
        n_id=n_id,
        q=q,
        beta=beta,
        xi=xi,
    )
    return prof[i]


def find_beta_scaled(d, n_id, q, xi, target_norm, shape_model="gsa"):
    """
    Same success test structure as your original script, but using
    lattice-estimator shape profiles for all shape models.
    """
    if d <= 0 or n_id < 0 or n_id > d:
        return infinity

    target_sigma = target_norm / sqrt(d)

    for beta in range(2, d // 2 + 1):
        gso_len = profile_len_at_index(
            i=d - beta,
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


# ============================================================
# Norm / scaling helpers
# ============================================================

def vector_typical_norm(dim, stddev):
    if dim <= 0:
        return 0.0
    return sqrt(dim) * stddev


def xi_factor(norm_s, norm_e):
    """
    Scale secret side so that xi * ||s|| ≈ ||e|| when secret is shorter.
    """
    if norm_s <= 0:
        return 1.0
    if norm_s < norm_e:
        return norm_e / norm_s
    return 1.0


# ============================================================
# Pattern probability helpers
# ============================================================

def log_binom(n, k):
    if k < 0 or k > n:
        return float("-inf")
    return lgamma(n + 1) - lgamma(k + 1) - lgamma(n - k + 1)


def single_hit_prob(n, d, k_pat):
    """
    p_k = C(n-k, d) / C(n, d)
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
    P_pat(k) ≈ 1 - (1 - p_k)^n
    """
    pk = single_hit_prob(n, d, k_pat)
    if pk <= 0.0:
        return 0.0
    if pk >= 1.0:
        return 1.0
    return 1.0 - exp(n * log(1.0 - pk))


def pattern_penalty_bits(n, d, k_pat):
    P = overall_pattern_success_prob(n, d, k_pat)
    if P <= 0.0:
        return float("inf")
    return -log(P, 2)


def pigeonhole_start_k(n, h):
    """
    Nontrivial search start:
      floor((n-1)/h) + 1
    """
    if h <= 0:
        return 1
    return max(1, floor((n - 1) / h) + 1)


# ============================================================
# Parsing
# ============================================================

def parse():
    parser = argparse.ArgumentParser()

    parser.add_argument("-n", type=int, required=True)
    parser.add_argument("-q", type=int, required=True)

    parser.add_argument("-e", type=str, required=True)
    parser.add_argument("--param_e", type=float, required=True)

    parser.add_argument("-s", type=str, required=True)
    parser.add_argument("--param_s", type=float, required=True)

    parser.add_argument("--proba", default=False, action=argparse.BooleanOptionalAction)
    parser.add_argument("--verbose", default=False, action=argparse.BooleanOptionalAction)

    parser.add_argument("--use_pattern", default=False, action=argparse.BooleanOptionalAction,
                        help="Enable pattern reduction before hybrid attack.")
    parser.add_argument("--pattern_hw", type=int, default=None,
                        help="Support weight h used in pattern probability. "
                             "If not given and secret is ternary, use param_s.")

    parser.add_argument("--use_scaling", default=False, action=argparse.BooleanOptionalAction,
                        help="Enable lattice scaling xi.")
    parser.add_argument("--xi", type=float, default=None,
                        help="Manual scaling factor xi. If not given, compute from norm balance.")

    parser.add_argument("--shape_model", type=str, default="gsa",
                        choices=["gsa", "zgsa", "cn11"])
    parser.add_argument("--cost_model", type=str, default="MATZOV",
                        choices=["MATZOV", "BDGL16", "ABLR21", "CheNgu12", "LaaMosPol14"])

    parser.add_argument("--kappa_max", type=int, default=100)
    parser.add_argument("--kpat_max", type=int, default=130)

    args = parser.parse_args()

    n = args.n
    q = args.q
    probabilistic_bkz = args.proba
    verbose = args.verbose

    e_dist = args.e.strip().lower()
    st_e = args.param_e
    std_e, D_e = build_law(e_dist, n, st_e)

    s_dist = args.s.strip().lower()
    st_s = args.param_s
    std_s, D_s = build_law(s_dist, n, st_s)

    use_pattern = args.use_pattern
    pattern_hw = args.pattern_hw
    if use_pattern and pattern_hw is None:
        if s_dist == "ternary":
            pattern_hw = int(st_s)
        else:
            sys.exit("When --use_pattern is enabled for non-ternary secret, "
                     "please provide --pattern_hw explicitly.")

    use_scaling = args.use_scaling
    xi_manual = args.xi

    shape_model = normalize_shape_model(args.shape_model)
    cost_model = normalize_cost_model(args.cost_model)

    return (
        n, q,
        e_dist, D_e, st_e, std_e,
        s_dist, D_s, st_s, std_s,
        probabilistic_bkz, verbose,
        use_pattern, pattern_hw,
        use_scaling, xi_manual,
        shape_model, cost_model,
        args.kappa_max, args.kpat_max
    )


# ============================================================
# Single-point evaluation
# ============================================================

def evaluate_point(n, q,
                   e_dist, st_e, std_e,
                   s_dist, st_s, std_s,
                   pattern_hw, probabilistic_bkz, use_pattern,
                   use_scaling, xi_manual,
                   xi_base, k_pat, kappa,
                   shape_model, cost_model):
    """
    Evaluate one fixed (k_pat, kappa).
    """

    if k_pat < 0 or kappa < 0:
        return {"ok": False}

    if use_pattern:
        if k_pat > n - pattern_hw:
            return {"ok": False}
    else:
        if k_pat != 0:
            return {"ok": False}

    # after pattern
    n_pat = n - k_pat
    if n_pat <= 0:
        return {"ok": False}

    # residual secret dimension after guessing
    n_id = n_pat - kappa
    if n_id <= 0:
        return {"ok": False}

    # total lattice dimension
    d_eff = n + n_id
    if d_eff <= 0:
        return {"ok": False}

    # entropy after pattern (for guess space size)
    std_s_pat, D_s_pat = build_law(s_dist, n_pat, st_s)
    H_pat = entropy(D_s_pat)

    # pattern penalty
    if use_pattern:
        penalty_pat = pattern_penalty_bits(n, pattern_hw, k_pat)
        if penalty_pat == float("inf"):
            return {"ok": False}
    else:
        penalty_pat = 0.0

    # scaling factor
    xi = xi_manual if xi_manual is not None else xi_base
    if not use_scaling:
        xi = 1.0
    if xi <= 0:
        return {"ok": False}

    # target norm
    norm_g = vector_typical_norm(n, std_e)

    # keep your original fixed-weight residual estimate for pattern+guess
    # since this is the structure you have been using
    h_res = int(ceil(pattern_hw * n_id / n_pat))
    h_res = min(h_res, n_id)
    norm_f_res = sqrt(h_res)

    target_norm = sqrt(norm_g**2 + (xi * norm_f_res)**2)

    # GH baseline filtering
    logvol = scaled_logvol(d_eff, n_id, q, xi)
    lambda1_est = gh_lambda1(d_eff, logvol)

    if target_norm > lambda1_est:
        return {
            "ok": False,
            "xi": xi,
            "target_norm": target_norm,
            "lambda1_est": lambda1_est,
            "penalty_pat": penalty_pat,
        }

    beta = find_beta_scaled(
        d=d_eff,
        n_id=n_id,
        q=q,
        xi=xi,
        target_norm=target_norm,
        shape_model=shape_model
    )

    if beta == infinity:
        return {
            "ok": False,
            "xi": xi,
            "target_norm": target_norm,
            "lambda1_est": lambda1_est,
            "penalty_pat": penalty_pat,
        }

    costBKZ = bkz_cost(beta, d_eff, alg=cost_model)

    _, costCVP = batchCVPP_cost(beta, H_pat * kappa, sqrt(4 / 3), 1)
    costHybrid = log2_add(costBKZ, costCVP)
    total_cost = costHybrid + penalty_pat

    return {
        "ok": True,
        "beta": beta,
        "costBKZ": costBKZ,
        "costCVP": costCVP,
        "penalty_pat": penalty_pat,
        "total_cost": total_cost,
        "log2|M|": H_pat * kappa,
        "H_pat": H_pat,
        "xi": xi,
        "target_norm": target_norm,
        "lambda1_est": lambda1_est,
        "n_id": n_id,
        "d_eff": d_eff,
    }


# ============================================================
# Fixed k_pat: exhaustive search on kappa
# ============================================================

def best_kappa_for_kpat(n, q,
                        e_dist, st_e, std_e,
                        s_dist, st_s, std_s,
                        pattern_hw, probabilistic_bkz, use_pattern,
                        use_scaling, xi_manual, xi_base,
                        k_pat, shape_model, cost_model,
                        kappa_min=0, kappa_max=100, verbose=False):
    best = None

    for kappa in range(kappa_min, kappa_max + 1):
        r = evaluate_point(
            n=n, q=q,
            e_dist=e_dist, st_e=st_e, std_e=std_e,
            s_dist=s_dist, st_s=st_s, std_s=std_s,
            pattern_hw=pattern_hw,
            probabilistic_bkz=probabilistic_bkz,
            use_pattern=use_pattern,
            use_scaling=use_scaling,
            xi_manual=xi_manual,
            xi_base=xi_base,
            k_pat=k_pat,
            kappa=kappa,
            shape_model=shape_model,
            cost_model=cost_model
        )

        if not r.get("ok", False):
            continue

        if (best is None) or (r["total_cost"] < best["total_cost"]):
            best = dict(r)
            best["kappa"] = kappa
            best["k_pat"] = k_pat

    if verbose and best is not None:
        print(
            f"[kappa-search] shape={shape_model}, cost={cost_model}, "
            f"k_pat={k_pat:3d}, best_kappa={best['kappa']:3d}, "
            f"log2M={best['log2|M|']:.4f}, "
            f"penalty_pat={best['penalty_pat']:.4f}, "
            f"xi={best['xi']:.6f}, "
            f"||v||={best['target_norm']:.4f}, "
            f"lambda1_GH={best['lambda1_est']:.4f}, "
            f"cost={best['total_cost']:.4f}"
        )

    return best


# ============================================================
# Exhaustive search on k_pat and kappa
# ============================================================

def best_kpat_exhaustive_search(n, q,
                                e_dist, st_e, std_e,
                                s_dist, st_s, std_s,
                                pattern_hw, probabilistic_bkz, use_pattern,
                                use_scaling, xi_manual, xi_base,
                                shape_model, cost_model,
                                kappa_max=100, kpat_max=130,
                                verbose=False):
    best = None

    def update_best(best, cand):
        if cand is None:
            return best
        if best is None or cand["total_cost"] < best["total_cost"]:
            return cand
        return best

    if use_pattern:
        kpat_min = pigeonhole_start_k(n, pattern_hw)
        kpat_max_eff = min(n - pattern_hw, kpat_max)
    else:
        kpat_min = 0
        kpat_max_eff = 0

    for k_pat in range(kpat_min, kpat_max_eff + 1):
        cand = best_kappa_for_kpat(
            n=n, q=q,
            e_dist=e_dist, st_e=st_e, std_e=std_e,
            s_dist=s_dist, st_s=st_s, std_s=std_s,
            pattern_hw=pattern_hw,
            probabilistic_bkz=probabilistic_bkz,
            use_pattern=use_pattern,
            use_scaling=use_scaling,
            xi_manual=xi_manual,
            xi_base=xi_base,
            k_pat=k_pat,
            shape_model=shape_model,
            cost_model=cost_model,
            kappa_min=0,
            kappa_max=kappa_max,
            verbose=verbose
        )
        best = update_best(best, cand)

    if verbose and best is not None:
        print(
            f"[kpat-search] exhaustive over k_pat in [{kpat_min},{kpat_max_eff}], "
            f"shape={shape_model}, cost={cost_model}, "
            f"best_kpat={best['k_pat']}, best_kappa={best['kappa']}, "
            f"cost={best['total_cost']:.4f}"
        )

    return best


# ============================================================
# Main estimator
# ============================================================

def estimate(n, q,
             e_dist, D_e, st_e, std_e,
             s_dist, D_s, st_s, std_s,
             probabilistic_bkz=False,
             verbose=False,
             use_pattern=False,
             pattern_hw=None,
             use_scaling=False,
             xi_manual=None,
             shape_model="gsa",
             cost_model="MATZOV",
             kappa_max=100,
             kpat_max=130):

    print(f"running the estimator with probabilistic_bkz = {probabilistic_bkz!r}")
    print(f"use_scaling = {use_scaling!r}, xi_manual = {xi_manual!r}")
    print(f"shape_model = {shape_model}, cost_model = {cost_model}")
    print('{:5s} {:8s} {:12s}{:14s} {:10s}{:10s}'.format(
        'n', 'kappa', 'BKZ only', 'bit sec.', 'Hybrid', 'bit sec.')
    )

    # baseline norm-balance xi
    norm_s_full = vector_typical_norm(n, std_s)
    norm_e_full = vector_typical_norm(n, std_e)
    xi_base = xi_factor(norm_s_full, norm_e_full)
    if not use_scaling:
        xi_base = 1.0
    if xi_manual is not None:
        xi_base = xi_manual

    # baseline: k_pat = 0
    d0 = 2 * n
    n_id0 = n

    norm_g0 = vector_typical_norm(n, std_e)
    norm_f0 = vector_typical_norm(n, std_s)
    target_norm0 = sqrt(norm_g0**2 + (xi_base * norm_f0)**2)

    logvol0 = scaled_logvol(d0, n_id0, q, xi_base)
    lambda10 = gh_lambda1(d0, logvol0)

    beta_nonhyb = find_beta_scaled(
        d=d0,
        n_id=n_id0,
        q=q,
        xi=xi_base,
        target_norm=target_norm0,
        shape_model=shape_model
    )
    if beta_nonhyb == infinity:
        beta_nonhyb = 2 * n

    cost_nonhyb = bkz_cost(beta_nonhyb, d0, alg=cost_model)

    # search
    best_cost = float("inf")
    best_beta = None
    best_kappa = 0
    best_kpat = 0
    best_xi = xi_base

    if use_pattern:
        best = best_kpat_exhaustive_search(
            n=n, q=q,
            e_dist=e_dist, st_e=st_e, std_e=std_e,
            s_dist=s_dist, st_s=st_s, std_s=std_s,
            pattern_hw=pattern_hw,
            probabilistic_bkz=probabilistic_bkz,
            use_pattern=use_pattern,
            use_scaling=use_scaling,
            xi_manual=xi_manual,
            xi_base=xi_base,
            shape_model=shape_model,
            cost_model=cost_model,
            kappa_max=kappa_max,
            kpat_max=kpat_max,
            verbose=verbose
        )
    else:
        best = best_kappa_for_kpat(
            n=n, q=q,
            e_dist=e_dist, st_e=st_e, std_e=std_e,
            s_dist=s_dist, st_s=st_s, std_s=std_s,
            pattern_hw=pattern_hw if pattern_hw is not None else int(st_s),
            probabilistic_bkz=probabilistic_bkz,
            use_pattern=False,
            use_scaling=use_scaling,
            xi_manual=xi_manual,
            xi_base=xi_base,
            k_pat=0,
            shape_model=shape_model,
            cost_model=cost_model,
            kappa_min=0,
            kappa_max=kappa_max,
            verbose=verbose
        )

    if best is not None:
        best_cost = best["total_cost"]
        best_beta = best["beta"]
        best_kappa = best["kappa"]
        best_kpat = best["k_pat"]
        best_xi = best["xi"]

    if best_beta is None:
        best_beta = beta_nonhyb
        best_cost = cost_nonhyb
        best_kappa = 0
        best_kpat = 0

    print('{:5s} {:8s} {:12s}{:14s} {:10s}{:10s}'.format(
        str(n),
        str(best_kappa),
        str(int(beta_nonhyb)),
        str(round(cost_nonhyb, 2)),
        str(int(best_beta)),
        str(round(best_cost, 2))
    ))

    if verbose:
        print(f"baseline xi                = {xi_base:.12f}")
        print(f"baseline ||v||             = {target_norm0:.12f}")
        print(f"baseline lambda1_GH        = {lambda10:.12f}")
        print(f"best pattern length k_pat  = {best_kpat}")
        print(f"best xi                    = {best_xi:.12f}")
        if use_pattern:
            Pbest = overall_pattern_success_prob(n, pattern_hw, best_kpat)
            print(f"P_pat(best k_pat)          = {Pbest:.12e}")


if __name__ == "__main__":
    (
        n, q,
        e_dist, D_e, st_e, std_e,
        s_dist, D_s, st_s, std_s,
        probabilistic_bkz, verbose,
        use_pattern, pattern_hw,
        use_scaling, xi_manual,
        shape_model, cost_model,
        kappa_max, kpat_max
    ) = parse()

    estimate(
        n, q,
        e_dist, D_e, st_e, std_e,
        s_dist, D_s, st_s, std_s,
        probabilistic_bkz=probabilistic_bkz,
        verbose=verbose,
        use_pattern=use_pattern,
        pattern_hw=pattern_hw,
        use_scaling=use_scaling,
        xi_manual=xi_manual,
        shape_model=shape_model,
        cost_model=cost_model,
        kappa_max=kappa_max,
        kpat_max=kpat_max
    )