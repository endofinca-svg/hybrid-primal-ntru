from math import sqrt
from sage.all import *

# ============================================================
# Load modular components
# ============================================================

load("hybrid estimator/config.sage")
load("hybrid estimator/dist_utils.sage")
load("hybrid estimator/cost_models.sage")
load("hybrid estimator/shape_models.sage")
load("hybrid estimator/pattern_utils.sage")
load("hybrid estimator/search_strategy.sage")
load("hybrid estimator/cli.sage")


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
    print(f"use_pattern = {use_pattern!r}, pattern_hw = {pattern_hw!r}")
    print(f"use_scaling = {use_scaling!r}, xi_manual = {xi_manual!r}")
    print(f"shape_model = {shape_model}, cost_model = {cost_model}")

    print('{:5s} {:8s} {:8s} {:12s}{:14s} {:10s}{:10s}'.format(
        'n', 'k_pat', 'kappa', 'BKZ only', 'bit sec.', 'Hybrid', 'bit sec.')
    )

    # ------------------------------------------------------------
    # Baseline norm-balance xi
    # ------------------------------------------------------------
    norm_s_full = vector_typical_norm(n, std_s)
    norm_e_full = vector_typical_norm(n, std_e)

    xi_base = xi_factor(norm_s_full, norm_e_full)

    if not use_scaling:
        xi_base = 1.0

    if xi_manual is not None:
        xi_base = xi_manual

    # ------------------------------------------------------------
    # Baseline: k_pat = 0, kappa = 0
    # ------------------------------------------------------------
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

    # ------------------------------------------------------------
    # Hybrid search
    # ------------------------------------------------------------
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
    else:
        best_cost = cost_nonhyb
        best_beta = beta_nonhyb
        best_kappa = 0
        best_kpat = 0
        best_xi = xi_base

    # ------------------------------------------------------------
    # Main output
    # ------------------------------------------------------------
    print('{:5s} {:8s} {:8s} {:12s}{:14s} {:10s}{:10s}'.format(
        str(n),
        str(best_kpat),
        str(best_kappa),
        str(int(beta_nonhyb)),
        str(round(cost_nonhyb, 2)),
        str(int(best_beta)),
        str(round(best_cost, 2))
    ))

    # ------------------------------------------------------------
    # Verbose output
    # ------------------------------------------------------------
    if verbose:
        print(f"baseline xi                = {xi_base:.12f}")
        print(f"baseline ||v||             = {target_norm0:.12f}")
        print(f"baseline lambda1_GH        = {lambda10:.12f}")
        print(f"best pattern length k_pat  = {best_kpat}")
        print(f"best kappa                 = {best_kappa}")
        print(f"best xi                    = {best_xi:.12f}")

        if use_pattern:
            Pbest = overall_pattern_success_prob(n, pattern_hw, best_kpat)
            print(f"P_pat(best k_pat)          = {Pbest:.12e}")


# ============================================================
# Entry point
# ============================================================

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