import sys
sys.path.append("/home/inca/lattice-estimator")

from estimator import *
from estimator import nd as ND
from estimator.reduction import RC
from math import log2
from sage.all import oo


# ============================================================
# Helpers
# ============================================================

def min_rop(res):
    best = None
    best_name = None

    for name, value in res.items():
        if "rop" not in value:
            continue

        rop = value["rop"]
        if rop == oo:
            continue

        if best is None or rop < best:
            best = rop
            best_name = name

    return best_name, best


def get_field(value, keys, default=None):
    for k in keys:
        if k in value:
            return value[k]
    return default


def make_discrete_gaussian(sigma, n):
    """
    BAT samples:
        f, g <- D_{R, sigma_f}

    Different lattice-estimator versions may use different constructors,
    so we try several possible argument names.
    """
    constructors = [
        lambda: ND.DiscreteGaussian(stddev=sigma, n=n),
        lambda: ND.DiscreteGaussian(sigma=sigma, n=n),
        lambda: ND.DiscreteGaussian(stddev=sigma),
        lambda: ND.DiscreteGaussian(sigma=sigma),
    ]

    last_error = None
    for cons in constructors:
        try:
            return cons()
        except Exception as e:
            last_error = e

    raise RuntimeError(f"Cannot construct ND.DiscreteGaussian: {last_error}")


# ============================================================
# Cost models
# ============================================================

class CoreSVP:
    """
    CoreSVP classical:
        cost = 2^(0.292 * beta)
    """
    def __call__(self, beta, d, B=None):
        return 2 ** (0.2920 * beta)


class BATPaperClassical:
    """
    BAT paper classical model:
        C_BKZ-beta = d * 2^(0.292 beta)

    Hence:
        log2(cost) = 0.292 beta + log2(d)
    """
    def __call__(self, beta, d, B=None):
        return 2 ** (0.2920 * beta + log2(d))


# ============================================================
# BAT parameters for key recovery
# ============================================================

BAT_PARAMS = [
    {
        "name": "BAT-512",
        "n": 512,
        "q": 257,
        "sigma_f": 0.596,
        "paper_key_beta": 475,
    },
    {
        "name": "BAT-1024",
        "n": 1024,
        "q": 769,
        "sigma_f": 0.659,
        "paper_key_beta": 933,
    },
]


# ============================================================
# BAT paper key-recovery sanity check
# ============================================================

def print_bat_paper_key_value(param):
    """
    BAT Table 5 gives the BKZ blocksize for key recovery.

    We only check the classical key-recovery value:
        log2(cost) = 0.292 * beta + log2(d)

    For key recovery, the natural NTRU dimension is d = 2n.
    """
    name = param["name"]
    n = param["n"]
    beta = param["paper_key_beta"]
    d = 2 * n

    classical = 0.2920 * beta + log2(d)

    print(f"\n==============================")
    print(f"{name}: BAT paper key-recovery model")
    print(f"==============================")
    print(f"n          = {n}")
    print(f"d approx   = {d}")
    print(f"paper beta = {beta}")
    print(f"classical  = 2^{classical:.2f}")


# ============================================================
# BAT key recovery with lattice-estimator
# ============================================================

def run_bat_key_recovery(param, cost_model, cost_name, shape_name):
    name = param["name"]
    n = param["n"]
    q = param["q"]
    sigma_f = param["sigma_f"]

    # BAT key distribution:
    #     f, g <- D_{R, sigma_f}
    Xf = make_discrete_gaussian(sigma_f, n)
    Xg = make_discrete_gaussian(sigma_f, n)

    params = NTRU.Parameters(
        n=n,
        q=q,
        Xs=Xf,
        Xe=Xg,
        tag=f"{name} key recovery [{cost_name}, {shape_name}]"
    )

    print(f"\n==============================")
    print(f"scheme      = {name}")
    print(f"attack      = key recovery")
    print(f"n, q        = {n}, {q}")
    print(f"sigma_f     = {sigma_f}")
    print(f"cost model  = {cost_name}")
    print(f"shape model = {shape_name}")
    print(f"==============================")

    try:
        res = NTRU.estimate(
            params,
            red_cost_model=cost_model,
            red_shape_model=shape_name
        )
    except Exception as e:
        print(f"[failed] {e}")
        return None

    best_name, best_rop = min_rop(res)

    print(f"\n[best]")
    if best_rop is not None:
        print(f"best attack = {best_name}")
        print(f"best rop    = 2^{log2(float(best_rop)):.2f}")
    else:
        print("no finite rop found")

    print(f"\n[all finite attacks]")
    for attack_name, value in res.items():
        if "rop" not in value or value["rop"] == oo:
            continue

        rop_log = log2(float(value["rop"]))

        beta = get_field(value, ["beta", "β", "bkz_block_size"], default=None)
        d = get_field(value, ["d", "dimension"], default=None)
        delta = get_field(value, ["delta", "δ"], default=None)

        extra = []
        if beta is not None:
            extra.append(f"beta={beta}")
        if d is not None:
            extra.append(f"d={d}")
        if delta is not None:
            extra.append(f"delta={delta}")

        if extra:
            print(f"{attack_name:20s} :: rop = 2^{rop_log:.2f}, " + ", ".join(extra))
        else:
            print(f"{attack_name:20s} :: rop = 2^{rop_log:.2f}")

    return res


# ============================================================
# Models to run
# ============================================================

cost_models = [
    ("MATZOV", RC.MATZOV),
    ("BDGL16", RC.BDGL16),
    ("CoreSVP", CoreSVP()),
    ("BATPaperClassical", BATPaperClassical()),
]

shape_models = [
    "gsa",
    "zgsa",
]


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":

    # 1. Check BAT paper's own key-recovery classical model.
    for param in BAT_PARAMS:
        print_bat_paper_key_value(param)

    # 2. Run lattice-estimator for BAT key recovery only.
    for param in BAT_PARAMS:
        for cost_name, cost_model in cost_models:
            for shape_name in shape_models:
                run_bat_key_recovery(param, cost_model, cost_name, shape_name)