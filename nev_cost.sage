import sys
sys.path.append("/home/inca/lattice-estimator")

from estimator import *
from estimator import nd as ND
from estimator.reduction import RC
from math import log2
from sage.all import oo


# ============================================================
# Utilities
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


# ============================================================
# Distribution helpers
# NEV uses B_1 for both f' and g in the NTRU public-key part.
#
# B_1 = centered binomial with eta = 1:
#   Pr[0] = 1/2, Pr[1] = Pr[-1] = 1/4.
# ============================================================
def CenteredBinomial_B1(n):
    """
    Construct B_1 distribution for lattice-estimator.

    Different versions of lattice-estimator may use slightly different
    constructor signatures, so we try several common forms.
    """
    if hasattr(ND, "CenteredBinomial"):
        try:
            return ND.CenteredBinomial(1, n=n)
        except TypeError:
            try:
                return ND.CenteredBinomial(1, n)
            except TypeError:
                return ND.CenteredBinomial(1)

    # Fallback: B_1 has Pr[+1]=Pr[-1]=1/4, Pr[0]=1/2.
    # This is a sparse ternary distribution with expected p=m=n/4.
    # It is only an approximation if the estimator fixes the exact weight.
    if hasattr(ND, "SparseTernary"):
        return ND.SparseTernary(p=round(n / 4), m=round(n / 4), n=n)

    raise RuntimeError("Cannot construct centered binomial distribution B_1.")


# ============================================================
# CoreSVP (ADPS16 classical): 2^(0.292 * beta)
# ============================================================
class CoreSVP:
    def __call__(self, beta, d, B=None):
        return 2 ** (0.2920 * beta)


# ============================================================
# NEV NTRU public-key parameters
#
# NEV public key:
#     h = g / (v f' + 1)
#
# NEV paper estimates the NTRU part by rewriting:
#     h = -v h f' + g mod q
#
# So the short vector is essentially:
#     (g, f', 1)
#
# For estimator baseline, v is public/invertible and does not change
# the distribution of the unknowns used in the estimate.
# Therefore we set:
#     Xs = B_1     for f'
#     Xe = B_1     for g
# ============================================================
NEV_NTRU_PARAMS = [
    {
        "name": "NEV-512 / NEV'-512 NTRU-SK",
        "n": 512,
        "q": 769,
        "paper_sk_bits": 145,
        "paper_bkz": 426,
    },
    {
        "name": "NEV-1024 / NEV'-1024 NTRU-SK",
        "n": 1024,
        "q": 769,
        "paper_sk_bits": 292,
        "paper_bkz": 953,
    },
]


def run_nev_ntru_with_model(param_set, cost_model, cost_name, shape_name):
    n = param_set["n"]
    q = param_set["q"]

    Xf = CenteredBinomial_B1(n)   # f' <- B_1
    Xg = CenteredBinomial_B1(n)   # g  <- B_1

    params = NTRU.Parameters(
        n=n,
        q=q,
        Xs=Xf,
        Xe=Xg,
        tag=f"{param_set['name']} [{cost_name}, {shape_name}]"
    )

    print("\n============================================================")
    print(f"scheme      = {param_set['name']}")
    print(f"n, q        = {n}, {q}")
    print(f"dist        = f' <- B_1, g <- B_1")
    print(f"cost model  = {cost_name}")
    print(f"shape model = {shape_name}")
    print(f"paper SK    = {param_set['paper_sk_bits']} bits")
    print(f"paper BKZ   = {param_set['paper_bkz']}")
    print("============================================================")

    res = NTRU.estimate(
        params,
        red_cost_model=cost_model,
        red_shape_model=shape_name
    )

    best_name, best_rop = min_rop(res)

    print("\n[best]")
    if best_rop is not None:
        print(f"best attack = {best_name}")
        print(f"best rop    = 2^{log2_cost(best_rop):.2f}")
        print(f"gap to paper= {log2_cost(best_rop) - param_set['paper_sk_bits']:+.2f} bits")
    else:
        print("No finite rop found.")

    print("\n[all attacks]")
    for name, value in res.items():
        if "rop" in value and value["rop"] != oo:
            line = f"{name:20s} :: rop = 2^{log2_cost(value['rop']):.2f}"

            # Print useful fields if present.
            for key in ["beta", "β", "d", "delta", "δ", "eta", "η"]:
                if key in value:
                    line += f", {key}={value[key]}"
            print(line)


# ============================================================
# Cost models
# Keep the same three models as in your DAWN script.
# ============================================================
cost_models = [
    ("MATZOV", RC.MATZOV),
    ("BDGL16", RC.BDGL16),
    ("CoreSVP", CoreSVP()),
]


# ============================================================
# Shape models
# Start with GSA / ZGSA, same as your current baseline script.
# If needed, add "cn11" later.
# ============================================================
shape_models = [
    "gsa",
    "zgsa",
]


for param_set in NEV_NTRU_PARAMS:
    for cost_name, cost_model in cost_models:
        for shape_name in shape_models:
            run_nev_ntru_with_model(
                param_set=param_set,
                cost_model=cost_model,
                cost_name=cost_name,
                shape_name=shape_name,
            )