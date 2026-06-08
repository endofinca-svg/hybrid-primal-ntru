import sys
sys.path.append("/home/inca/lattice-estimator")

from estimator import *
from estimator import nd as ND
from estimator.reduction import RC
from math import log2
from sage.all import oo


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


# ============================================================
# CoreSVP (ADPS16 classical): 2^(0.292 * beta)
# ============================================================
class CoreSVP:
    def __call__(self, beta, d, B=None):
        return 2 ** (0.2920 * beta)


def run_with_model(cost_model, cost_name, shape_name):
    n = 512
    q = 257
    Xf = ND.SparseTernary(p=32, m=32, n=n)
    Xg = ND.SparseTernary(p=64, m=64, n=n)

    params = NTRU.Parameters(
        n=n,
        q=q,
        Xs=Xf,
        Xe=Xg,
        tag=f"DAWN-beta-512 NTRU [{cost_name}, {shape_name}]"
    )

    print(f"\n==============================")
    print(f"cost model  = {cost_name}")
    print(f"shape model = {shape_name}")
    print(f"==============================")

    res = NTRU.estimate(
        params,
        red_cost_model=cost_model,
        red_shape_model=shape_name
    )

    best_name, best_rop = min_rop(res)

    print(f"\n[best]")
    print(f"best attack = {best_name}")
    print(f"best rop    = 2^{log2(float(best_rop)):.2f}")

    print(f"\n[all attacks]")
    for name, value in res.items():
        if "rop" in value and value["rop"] != oo:
            print(f"{name:20s} :: rop = 2^{log2(float(value['rop'])):.2f}")


# ============================================================
# Only keep 3 cost models
# ============================================================
cost_models = [
    ("MATZOV", RC.MATZOV),
    ("BDGL16", RC.BDGL16),
    ("CoreSVP", CoreSVP()),   # 新加
]


# ============================================================
# Only keep GSA / ZGSA
# ============================================================
shape_models = [
    "gsa",
    "zgsa",
]


for cost_name, cost_model in cost_models:
    for shape_name in shape_models:
        run_with_model(cost_model, cost_name, shape_name)