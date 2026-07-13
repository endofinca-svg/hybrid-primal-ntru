from math import sqrt
import csv
import os
from sage.all import *

# ============================================================
# Load modular components
# ============================================================

load("hybrid_estimator/config.sage")
load("hybrid_estimator/dist_utils.sage")
load("hybrid_estimator/cost_models.sage")
load("hybrid_estimator/shape_models.sage")
load("hybrid_estimator/pattern_utils.sage")
load("hybrid_estimator/search_strategy.sage")
load("hybrid_estimator/cli.sage")


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
             kpat_max=130,
             lower_order_bits=0.0):

    print(f"running the estimator with probabilistic_bkz = {probabilistic_bkz!r}")
    print(f"use_pattern = {use_pattern!r}, pattern_hw = {pattern_hw!r}")
    print(f"use_scaling = {use_scaling!r}, xi_manual = {xi_manual!r}")
    print(f"shape_model = {shape_model}, cost_model = {cost_model}")
    print(f"slicer_lower_order_bits = {lower_order_bits}")

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
            lower_order_bits=lower_order_bits,
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
            lower_order_bits=lower_order_bits,
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

    if best is not None:
        best["beta_nonhyb"] = beta_nonhyb
        best["cost_nonhyb"] = cost_nonhyb
        best["slicer_lower_order_bits"] = lower_order_bits

    return best


def dominant_component(margin):
    if margin > 1:
        return "BKZ-dominated"
    if margin < -1:
        return "BDD-dominated"
    return "near-balanced"


def result_row(parameter_set, shape_model, cost_model, delta, best):
    c_bkz = best["red_cost"]
    c_bdd_without_delta = best["bdd_cost_without_delta"]
    c_bdd_with_delta = best["bdd_cost_with_delta"]
    margin = c_bkz - c_bdd_with_delta

    return {
        "Parameter set": parameter_set,
        "Shape model": shape_model.upper(),
        "Cost model": cost_model,
        "Delta": delta,
        "k_pat": best["k_pat"],
        "kappa": best["kappa"],
        "beta": best["beta"],
        "Penalty": best["penalty_pat"],
        "log2|M|": best["log2|M|"],
        "C_BKZ": c_bkz,
        "C_BDD_without_delta": c_bdd_without_delta,
        "C_BDD_with_delta": c_bdd_with_delta,
        "margin": margin,
        "Dominant component": dominant_component(margin),
        "Cost": best["total_cost"],
    }


def print_csv_table(rows):
    header = (
        "Parameter set | Shape | Cost model | Delta | k_pat | kappa | beta | "
        "Penalty | log2|M| | C_BKZ | C_BDD | C_BDD+Delta | margin | Dominant | Cost"
    )
    print(header)
    print("-" * len(header))

    for row in rows:
        print(
            f"{row['Parameter set']} | {row['Shape model']} | {row['Cost model']} | "
            f"{row['Delta']:.1f} | {row['k_pat']} | {row['kappa']} | {row['beta']} | "
            f"{row['Penalty']:.4f} | {row['log2|M|']:.4f} | "
            f"{row['C_BKZ']:.4f} | {row['C_BDD_without_delta']:.4f} | "
            f"{row['C_BDD_with_delta']:.4f} | "
            f"{row['margin']:.4f} | {row['Dominant component']} | {row['Cost']:.4f}"
        )


def write_results_csv(rows, output):
    fieldnames = [
        "Parameter set",
        "Shape model",
        "Cost model",
        "Delta",
        "k_pat",
        "kappa",
        "beta",
        "Penalty",
        "log2|M|",
        "C_BKZ",
        "C_BDD_without_delta",
        "C_BDD_with_delta",
        "margin",
        "Dominant component",
        "Cost",
    ]

    outdir = os.path.dirname(output)
    if outdir:
        os.makedirs(outdir, exist_ok=True)

    with open(output, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def run_sensitivity_grid(n, q,
                         e_dist, D_e, st_e, std_e,
                         s_dist, D_s, st_s, std_s,
                         probabilistic_bkz,
                         use_pattern, pattern_hw,
                         use_scaling, xi_manual,
                         parameter_set,
                         shape_models, cost_models,
                         lower_order_bits_values,
                         kappa_max, kpat_max,
                         output,
                         verbose=False):
    rows = []

    if parameter_set is None:
        parameter_set = "custom"

    for shape_model in shape_models:
        for cost_model in cost_models:
            for delta in lower_order_bits_values:
                print(
                    f"[sensitivity] parameter_set={parameter_set}, "
                    f"shape={shape_model}, cost={cost_model}, delta={delta}"
                )

                best = estimate(
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
                    kpat_max=kpat_max,
                    lower_order_bits=delta
                )

                if best is None:
                    raise RuntimeError(
                        f"No feasible result for shape={shape_model}, "
                        f"cost={cost_model}, delta={delta}"
                    )

                rows.append(
                    result_row(
                        parameter_set=parameter_set,
                        shape_model=shape_model,
                        cost_model=cost_model,
                        delta=delta,
                        best=best
                    )
                )

    print_csv_table(rows)

    if output is not None:
        write_results_csv(rows, output)
        print(f"[sensitivity] wrote CSV: {output}")

    return rows


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
        kappa_max, kpat_max,
        param_set_name,
        shape_models, cost_models,
        lower_order_bits_values,
        output
    ) = parse()

    run_grid = (
        param_set_name is not None
        or output is not None
        or len(shape_models) > 1
        or len(cost_models) > 1
        or len(lower_order_bits_values) > 1
    )

    if run_grid:
        if output is None and param_set_name == "DAWN-beta-512":
            output = "results/dawn_beta_512_slicer_od_sensitivity.csv"

        run_sensitivity_grid(
            n, q,
            e_dist, D_e, st_e, std_e,
            s_dist, D_s, st_s, std_s,
            probabilistic_bkz=probabilistic_bkz,
            use_pattern=use_pattern,
            pattern_hw=pattern_hw,
            use_scaling=use_scaling,
            xi_manual=xi_manual,
            parameter_set=param_set_name,
            shape_models=shape_models,
            cost_models=cost_models,
            lower_order_bits_values=lower_order_bits_values,
            kappa_max=kappa_max,
            kpat_max=kpat_max,
            output=output,
            verbose=verbose
        )
    else:
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
            kpat_max=kpat_max,
            lower_order_bits=lower_order_bits_values[0]
        )
