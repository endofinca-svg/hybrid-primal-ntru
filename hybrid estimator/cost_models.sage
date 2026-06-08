from math import log2

# ============================================================
# Cost model helpers
# Only keep: MATZOV / BDGL16 / CoreSVP
# ============================================================

def CoreSVP(beta, d):
    """
    CoreSVP (ADPS16 classical):
        cost = 2^(0.292 * beta)
    """
    beta = int(round(beta))
    return 2 ** (0.2920 * beta)


def BDGL16(beta, d):
    """
    BDGL16 asymptotic BKZ cost:
        cost = 2^(0.292 * beta + log2(8d) + 16.4)
    """
    beta = int(round(beta))
    log_cost = 0.2920 * beta + log2(8 * d) + 16.4
    return 2 ** log_cost


COST_MODELS = {
    "MATZOV": RC.MATZOV,
    "BDGL16": BDGL16,
    "CoreSVP": CoreSVP,
}


def normalize_cost_model(s):
    """
    Normalize cost model name.
    """
    s = s.strip()
    for k in COST_MODELS:
        if s.lower() == k.lower():
            return k
    raise ValueError(f"Unknown cost model: {s}")


def bkz_cost(beta, d, alg="MATZOV"):
    """
    Return log2 BKZ / SVP cost.
    """
    beta = int(round(beta))
    alg = normalize_cost_model(alg)

    cfun = COST_MODELS[alg]
    c = cfun(beta, d)

    return log2(c)