import sys
sys.path.insert(0, "/home/inca/lattice-estimator")

from estimator import *
from estimator import nd as ND
from estimator.reduction import RC

from math import log2
from sage.all import oo


# ============================================================
# Cost models
# ============================================================

def CoreSVP(beta, d, *args, **kwargs):
    """
    CoreSVP classical cost:
        cost = 2^(0.292 * beta)
    """
    beta = int(beta)
    return 2 ** (0.2920 * beta)


def BDGL16(beta, d, *args, **kwargs):
    """
    BDGL16 asymptotic BKZ cost:
        cost = 2^(0.292 * beta + log2(8d) + 16.4)
    """
    beta = int(beta)
    d = int(d)
    log_cost = 0.2920 * beta + log2(8 * d) + 16.4
    return 2 ** log_cost


COST_MODELS = {
    "MATZOV": RC.MATZOV,
    "BDGL16": BDGL16,
    "CoreSVP": CoreSVP,
}


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


def log2_cost(x):
    return log2(float(x))


def make_params(name, n, q, kg, kf):
    """
    DAWN Table 6:
        kg -> g
        kf -> f

    In lattice-estimator:
        Xs -> f
        Xe -> g
    """
    Xf = ND.SparseTernary(p=kf, m=kf, n=n)
    Xg = ND.SparseTernary(p=kg, m=kg, n=n)

    return NTRU.Parameters(
        n=n,
        q=q,
        Xs=Xf,
        Xe=Xg,
        tag=f"{name} NTRU"
    )


# ============================================================
# DAWN parameter sets
# ============================================================

PARAM_SETS = [
    ("DAWN-alpha-512",  512,  769, 160, 64),
    ("DAWN-beta-512",   512,  257,  64, 32),
    ("DAWN-alpha-1024", 1024, 769, 256, 96),
    ("DAWN-beta-1024",  1024, 257,  96, 64),
]


# 这里只跑 GSA
SHAPE_MODEL = "gsa"


def run_all():
    for pname, n, q, kg, kf in PARAM_SETS:
        params = make_params(pname, n, q, kg, kf)

        print("\n============================================================")
        print(f"{pname}: n={n}, q={q}, kg={kg}, kf={kf}")
        print("============================================================")

        for cname, cmodel in COST_MODELS.items():
            print(f"\n[cost model = {cname}, shape model = {SHAPE_MODEL}]")

            res = NTRU.estimate(
                params,
                red_cost_model=cmodel,
                red_shape_model=SHAPE_MODEL
            )

            best_name, best_rop = min_rop(res)

            print("\n[best]")
            if best_rop is None:
                print("No finite rop found.")
            else:
                print(f"best attack = {best_name}")
                print(f"best rop    = 2^{log2_cost(best_rop):.2f}")

            print("\n[all attacks]")
            for name, value in res.items():
                if "rop" in value and value["rop"] != oo:
                    line = f"{name:20s} :: rop = 2^{log2_cost(value['rop']):.2f}"

                    for key in ["beta", "β", "d", "delta", "δ", "eta", "η"]:
                        if key in value:
                            line += f", {key}={value[key]}"

                    print(line)

        print("\n")


if __name__ == "__main__":
    run_all()