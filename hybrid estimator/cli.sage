import argparse
import sys

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

    parser.add_argument(
        "--use_pattern",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Enable pattern reduction before hybrid attack."
    )

    parser.add_argument(
        "--pattern_hw",
        type=int,
        default=None,
        help="Support weight h used in pattern probability. "
             "If not given and secret is ternary, use param_s."
    )

    parser.add_argument(
        "--use_scaling",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Enable lattice scaling xi."
    )

    parser.add_argument(
        "--xi",
        type=float,
        default=None,
        help="Manual scaling factor xi. If not given, compute from norm balance."
    )

    parser.add_argument(
        "--shape_model",
        type=str,
        default="gsa",
        choices=["gsa", "zgsa"]
    )

    parser.add_argument(
        "--cost_model",
        type=str,
        default="MATZOV",
        choices=["MATZOV", "BDGL16", "CoreSVP"]
    )

    parser.add_argument("--kappa_max", type=int, default=100)
    parser.add_argument("--kpat_max", type=int, default=130)

    args = parser.parse_args()

    n = args.n
    q = args.q

    probabilistic_bkz = args.proba
    verbose = args.verbose

    # ------------------------------------------------------------
    # Error distribution
    # ------------------------------------------------------------
    e_dist = args.e.strip().lower()
    st_e = args.param_e
    std_e, D_e = build_law(e_dist, n, st_e)

    # ------------------------------------------------------------
    # Secret distribution
    # ------------------------------------------------------------
    s_dist = args.s.strip().lower()
    st_s = args.param_s
    std_s, D_s = build_law(s_dist, n, st_s)

    # # ------------------------------------------------------------
    # # Pattern
    # # ------------------------------------------------------------
    # use_pattern = args.use_pattern
    # pattern_hw = args.pattern_hw

    # if use_pattern and pattern_hw is None:
    #     if s_dist == "ternary":
    #         pattern_hw = int(st_s)
    #     else:
    #         sys.exit(
    #             "When --use_pattern is enabled for non-ternary secret, "
    #             "please provide --pattern_hw explicitly."
    #         )

    # ------------------------------------------------------------
    # Pattern
    # ------------------------------------------------------------
    use_pattern = args.use_pattern
    pattern_hw = args.pattern_hw

    if use_pattern and pattern_hw is None:
        if s_dist == "ternary":
            # Fixed-weight ternary: param_s is the true Hamming weight.
            pattern_hw = int(st_s)
        else:
            # Binomial / Gaussian: approximate by effective Hamming weight.
            pattern_hw = effective_hamming_weight(n, D_s)

        pattern_hw = max(0, min(n, pattern_hw))

        if verbose:
            print(
                f"[pattern] pattern_hw not provided; "
                f"use pattern_hw={pattern_hw} for secret distribution '{s_dist}'."
            )

    # ------------------------------------------------------------
    # Scaling
    # ------------------------------------------------------------
    use_scaling = args.use_scaling
    xi_manual = args.xi

    # ------------------------------------------------------------
    # Models
    # ------------------------------------------------------------
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