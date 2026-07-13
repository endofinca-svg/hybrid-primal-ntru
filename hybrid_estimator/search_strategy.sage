from math import sqrt, ceil
from sage.all import infinity

# ============================================================
# Single-point evaluation
# ============================================================

def evaluate_point(n, q,
                   e_dist, st_e, std_e,
                   s_dist, st_s, std_s,
                   pattern_hw, probabilistic_bkz, use_pattern,
                   use_scaling, xi_manual,
                   xi_base, k_pat, kappa,
                   shape_model, cost_model,
                   lower_order_bits=0.0):
    """
    Evaluate one fixed pair (k_pat, kappa).

    k_pat:
      pattern length, namely the number of coordinates removed from the sparse side.

    kappa:
      tail guessing dimension after pattern reduction.
    """

    if k_pat < 0 or kappa < 0:
        return {"ok": False}

    if use_pattern:
        if k_pat > n - pattern_hw:
            return {"ok": False}
    else:
        if k_pat != 0:
            return {"ok": False}

    # ------------------------------------------------------------
    # Dimension after pattern
    # ------------------------------------------------------------
    n_pat = n - k_pat

    if n_pat <= 0:
        return {"ok": False}

    # ------------------------------------------------------------
    # Residual secret dimension after tail guessing
    # ------------------------------------------------------------
    n_id = n_pat - kappa

    if n_id <= 0:
        return {"ok": False}

    # ------------------------------------------------------------
    # Effective lattice dimension
    # q-block remains n, identity/scaled block becomes n_id.
    # ------------------------------------------------------------
    d_eff = n + n_id

    if d_eff <= 0:
        return {"ok": False}

    # ------------------------------------------------------------
    # Entropy after pattern
    # This determines the size of the batch target set.
    # ------------------------------------------------------------
    std_s_pat, D_s_pat = build_law(s_dist, n_pat, st_s)
    H_pat = entropy(D_s_pat)

    # # ------------------------------------------------------------
    # # Pattern penalty
    # # ------------------------------------------------------------
    # if use_pattern:
    #     penalty_pat = pattern_penalty_bits(n, pattern_hw, k_pat)

    #     if penalty_pat == float("inf"):
    #         return {"ok": False}
    # else:
    #     penalty_pat = 0.0

    # # ------------------------------------------------------------
    # # Scaling factor
    # # ------------------------------------------------------------
    # xi = xi_manual if xi_manual is not None else xi_base

    # if not use_scaling:
    #     xi = 1.0

    # if xi <= 0:
    #     return {"ok": False}

    # # ------------------------------------------------------------
    # # Target norm
    # # ------------------------------------------------------------
    # norm_g = vector_typical_norm(n, std_e)

    # # The sparse side is fixed-weight ternary in the main NTRU setting.
    # # After pattern and tail guessing, estimate the remaining weight by:
    # #
    # #     h_res ≈ pattern_hw * n_id / n_pat.
    # #
    # h_res = int(ceil(pattern_hw * n_id / n_pat))
    # h_res = min(h_res, n_id)

    # norm_f_res = sqrt(h_res)

    # target_norm = sqrt(norm_g**2 + (xi * norm_f_res)**2)

    # # ------------------------------------------------------------
    # # Gaussian heuristic filtering
    # # ------------------------------------------------------------
    # logvol = scaled_logvol(d_eff, n_id, q, xi)
    # lambda1_est = gh_lambda1(d_eff, logvol)

    # ------------------------------------------------------------
    # Pattern penalty
    # ------------------------------------------------------------
    if use_pattern:
        penalty_pat = pattern_penalty_bits(n, pattern_hw, k_pat)

        if penalty_pat == float("inf"):
            return {"ok": False}
    else:
        penalty_pat = 0.0

    # ------------------------------------------------------------
    # Target norm and residual secret-side statistics
    # ------------------------------------------------------------
    norm_g = vector_typical_norm(n, std_e)

    norm_f_res, sigma_f_res_eff = residual_secret_stats(
        s_dist=s_dist,
        n_id=n_id,
        n_pat=n_pat,
        pattern_hw=pattern_hw,
        std_s=std_s,
    )

    if norm_f_res is None or sigma_f_res_eff is None:
        return {"ok": False}

    # ------------------------------------------------------------
    # Scaling factor
    # ------------------------------------------------------------
    if use_scaling:
        if xi_manual is not None:
            xi = xi_manual
        else:
            sigma_g_eff = norm_g / sqrt(n)
            xi = sigma_g_eff / sigma_f_res_eff
    else:
        xi = 1.0

    if xi <= 0:
        return {"ok": False}

    # ------------------------------------------------------------
    # Target norm
    # ------------------------------------------------------------
    target_norm = sqrt(norm_g**2 + (xi * norm_f_res)**2)

    # ------------------------------------------------------------
    # Gaussian heuristic filtering
    # ------------------------------------------------------------
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

    # ------------------------------------------------------------
    # Find beta using lattice-estimator shape profile
    # ------------------------------------------------------------
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

    # ------------------------------------------------------------
    # Cost calculation
    # ------------------------------------------------------------
    costBKZ = bkz_cost(beta, d_eff, alg=cost_model)

    log2M = H_pat * kappa

    probCVP, costCVP = batchCVPP_cost(
        beta,
        log2M,
        sqrt(4 / 3),
        1,
        lower_order_bits=lower_order_bits
    )
    costCVP_without_delta = costCVP - lower_order_bits

    costHybrid = log2_add(costBKZ, costCVP)

    total_cost = costHybrid + penalty_pat

    # Pattern success probability, for logging.
    # If penalty_pat = -log2(P_pat), then P_pat = 2^{-penalty_pat}.
    P_pat = 2 ** (-penalty_pat)

    ret = {
        "ok": True,
        "d": d_eff,
        "n_pat": n_pat,
        "n_id": n_id,
        "beta": beta,
        "log2|M|": log2M,
        "H_pat": H_pat,
        "penalty_pat": penalty_pat,
        "P_pat": P_pat,
        "xi": xi,
        "target_norm": target_norm,
        "lambda1_est": lambda1_est,
        "red_cost": costBKZ,
        "bdd_cost_without_delta": costCVP_without_delta,
        "bdd_cost_with_delta": costCVP,
        "batchcvpp_prob": probCVP,
        "slicer_lower_order_bits": lower_order_bits,
        "md_cost": costCVP,
        "hybrid_cost": costHybrid,
        "total_cost": total_cost,
    }

    return ret


# ============================================================
# Fixed k_pat: exhaustive search on kappa
# ============================================================

def best_kappa_for_kpat(n, q,
                        e_dist, st_e, std_e,
                        s_dist, st_s, std_s,
                        pattern_hw, probabilistic_bkz, use_pattern,
                        use_scaling, xi_manual, xi_base,
                        k_pat, shape_model, cost_model,
                        kappa_min=0, kappa_max=100,
                        lower_order_bits=0.0,
                        verbose=False):
    """
    For a fixed k_pat, search the best kappa.

    For each candidate kappa, we evaluate the tradeoff:

        larger kappa:
            - decreases the final lattice dimension d;
            - decreases the remaining secret norm;
            - increases the batch size log2|M|;
            - increases the multi-decoding cost.

    The best kappa is the feasible one minimizing total_cost.
    """

    best = None

    # ------------------------------------------------------------
    # Dimensions after pattern
    # ------------------------------------------------------------
    if use_pattern:
        n_pat = n - k_pat
    else:
        n_pat = n

    if n_pat <= 0:
        return None

    for kappa in range(kappa_min, kappa_max + 1):

        # --------------------------------------------------------
        # Basic feasibility checks
        # --------------------------------------------------------
        if kappa < 0:
            continue

        # Tail guessing cannot exceed the remaining sparse side.
        if kappa >= n_pat:
            continue

        # Remaining identity/sparse dimension after tail guessing.
        n_id = n_pat - kappa

        # Final lattice dimension:
        #
        #   q-block dimension      : n
        #   remaining sparse block : n_id
        #
        # so d = n + n_id = 2n - k_pat - kappa.
        #
        d = n + n_id

        if n_id <= 0 or d <= 0:
            continue

        # --------------------------------------------------------
        # Evaluate this point
        # --------------------------------------------------------
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
            cost_model=cost_model,
            lower_order_bits=lower_order_bits
        )

        if not r.get("ok", False):
            continue

        # Store dimensions explicitly, even if evaluate_point
        # does not return them.
        r["n_pat"] = n_pat
        r["n_id"] = n_id
        r["d"] = d

        if (best is None) or (r["total_cost"] < best["total_cost"]):
            best = dict(r)
            best["kappa"] = kappa
            best["k_pat"] = k_pat

    # ------------------------------------------------------------
    # Verbose output: tradeoff summary for the best kappa
    # ------------------------------------------------------------
    if verbose and best is not None:

        # Use .get(...) to avoid KeyError if some fields are not
        # currently returned by evaluate_point.
        beta = best.get("beta", None)
        red_cost = best.get("red_cost", best.get("T_red", None))
        md_cost = best.get("md_cost", best.get("T_md", None))
        total_cost = best.get("total_cost", None)

        log2M = best.get("log2|M|", best.get("log2M", None))
        penalty_pat = best.get("penalty_pat", None)
        p_pat = best.get("P_pat", best.get("p_pat", None))

        target_norm = best.get("target_norm", None)
        lambda1_est = best.get("lambda1_est", None)
        xi = best.get("xi", None)

        n_pat_best = best.get("n_pat", n - k_pat)
        n_id_best = best.get("n_id", n_pat_best - best["kappa"])
        d_best = best.get("d", n + n_id_best)

        msg = (
            f"[kappa-search] shape={shape_model}, cost={cost_model}, "
            f"k_pat={k_pat:3d}, best_kappa={best['kappa']:3d}, "
            f"n_pat={n_pat_best:4d}, n_id={n_id_best:4d}, d={d_best:4d}"
        )

        if beta is not None:
            msg += f", beta={beta:3d}"

        if log2M is not None:
            msg += f", log2M={log2M:.4f}"

        if penalty_pat is not None:
            msg += f", penalty_pat={penalty_pat:.4f}"

        if p_pat is not None:
            msg += f", P_pat={p_pat:.4e}"

        if xi is not None:
            msg += f", xi={xi:.6f}"

        if target_norm is not None:
            msg += f", ||v||={target_norm:.4f}"

        if lambda1_est is not None:
            msg += f", lambda1_GH={lambda1_est:.4f}"

        if red_cost is not None:
            msg += f", red={red_cost:.4f}"

        if md_cost is not None:
            msg += f", md={md_cost:.4f}"

        if total_cost is not None:
            msg += f", cost={total_cost:.4f}"

        print(msg)

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
                                lower_order_bits=0.0,
                                verbose=False):
    """
    Exhaustively search over k_pat and kappa.
    """
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
            lower_order_bits=lower_order_bits,
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
