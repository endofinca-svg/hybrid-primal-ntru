import argparse
import sys

# ============================================================
# Parsing
# ============================================================

PARAMETER_SETS = {
    "DAWN-beta-512": {
        "n": 512,
        "q": 257,
        "e": "ternary",
        "param_e": 128,
        "s": "ternary",
        "param_s": 64,
        "pattern_hw": 64,
        "use_pattern": True,
        "use_scaling": True,
        "kappa_max": 100,
        "kpat_max": 100,
    },
}

PARAMETER_SET_ALIASES = {
    "dawn-beta-512": "DAWN-beta-512",
    "dawn-beta512": "DAWN-beta-512",
    "dawn-\u03b2-512": "DAWN-beta-512",
}


def normalize_param_set_name(s):
    key = s.strip().lower()
    key = key.replace(" ", "-")
    key = key.replace("_", "-")

    if key in PARAMETER_SET_ALIASES:
        return PARAMETER_SET_ALIASES[key]

    raise ValueError(f"Unknown parameter set: {s}")


def parse_csv_list(s):
    return [x.strip() for x in s.split(",") if x.strip()]


def parse_float_csv_list(s):
    vals = []

    for x in parse_csv_list(s):
        vals.append(float(x))

    if not vals:
        raise ValueError("Expected at least one lower-order bit value.")

    return vals


def choose_arg(args, defaults, name, fallback=None):
    val = getattr(args, name)

    if val is not None:
        return val

    if defaults is not None and name in defaults:
        return defaults[name]

    if fallback is not None:
        return fallback

    sys.exit(f"Missing required argument: {name}")


def parse():
    parser = argparse.ArgumentParser()

    parser.add_argument("--param-set", type=str, default=None)

    parser.add_argument("-n", type=int, default=None)
    parser.add_argument("-q", type=int, default=None)

    parser.add_argument("-e", type=str, default=None)
    parser.add_argument("--param_e", type=float, default=None)

    parser.add_argument("-s", type=str, default=None)
    parser.add_argument("--param_s", type=float, default=None)

    parser.add_argument("--proba", default=False, action=argparse.BooleanOptionalAction)
    parser.add_argument("--verbose", default=False, action=argparse.BooleanOptionalAction)

    parser.add_argument(
        "--use_pattern", "--use-pattern",
        dest="use_pattern",
        default=None,
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
        "--use_scaling", "--use-scaling",
        dest="use_scaling",
        default=None,
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
        "--shape_model", "--shape-model",
        dest="shape_model",
        type=str,
        default="gsa"
    )

    parser.add_argument(
        "--cost_model", "--cost-model",
        dest="cost_model",
        type=str,
        default="MATZOV"
    )

    parser.add_argument("--shape-models", type=str, default=None)
    parser.add_argument("--cost-models", type=str, default=None)

    parser.add_argument("--slicer-lower-order-bits", type=str, default="0")
    parser.add_argument("--output", type=str, default=None)

    parser.add_argument("--kappa_max", "--kappa-max", dest="kappa_max", type=int, default=None)
    parser.add_argument("--kpat_max", "--kpat-max", dest="kpat_max", type=int, default=None)

    args = parser.parse_args()

    param_set_name = None
    param_defaults = None

    if args.param_set is not None:
        try:
            param_set_name = normalize_param_set_name(args.param_set)
        except ValueError as e:
            sys.exit(str(e))

        param_defaults = PARAMETER_SETS[param_set_name]

    n = int(choose_arg(args, param_defaults, "n"))
    q = int(choose_arg(args, param_defaults, "q"))

    probabilistic_bkz = args.proba
    verbose = args.verbose

    # ------------------------------------------------------------
    # Error distribution
    # ------------------------------------------------------------
    e_dist = choose_arg(args, param_defaults, "e").strip().lower()
    st_e = choose_arg(args, param_defaults, "param_e")
    std_e, D_e = build_law(e_dist, n, st_e)

    # ------------------------------------------------------------
    # Secret distribution
    # ------------------------------------------------------------
    s_dist = choose_arg(args, param_defaults, "s").strip().lower()
    st_s = choose_arg(args, param_defaults, "param_s")
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
    if args.use_pattern is not None:
        use_pattern = args.use_pattern
    elif param_defaults is not None:
        use_pattern = param_defaults.get("use_pattern", False)
    else:
        use_pattern = False

    if args.pattern_hw is not None:
        pattern_hw = args.pattern_hw
    elif param_defaults is not None:
        pattern_hw = param_defaults.get("pattern_hw", None)
    else:
        pattern_hw = None

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
    if args.use_scaling is not None:
        use_scaling = args.use_scaling
    elif param_defaults is not None:
        use_scaling = param_defaults.get("use_scaling", False)
    else:
        use_scaling = False

    xi_manual = args.xi

    # ------------------------------------------------------------
    # Models
    # ------------------------------------------------------------
    shape_model = normalize_shape_model(args.shape_model)
    cost_model = normalize_cost_model(args.cost_model)

    if args.shape_models is not None:
        shape_models = [
            normalize_shape_model(s)
            for s in parse_csv_list(args.shape_models)
        ]
    else:
        shape_models = [shape_model]

    if args.cost_models is not None:
        cost_models = [
            normalize_cost_model(s)
            for s in parse_csv_list(args.cost_models)
        ]
    else:
        cost_models = [cost_model]

    try:
        lower_order_bits_values = parse_float_csv_list(args.slicer_lower_order_bits)
    except ValueError as e:
        sys.exit(str(e))

    kappa_max = choose_arg(args, param_defaults, "kappa_max", fallback=100)
    kpat_max = choose_arg(args, param_defaults, "kpat_max", fallback=130)

    return (
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
        args.output
    )
