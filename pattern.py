import math
import numpy as np
import matplotlib.pyplot as plt


# =========================
# 基础组合函数（用 log 防止溢出）
# =========================
def log_binom(n, k):
    if k < 0 or k > n:
        return float("-inf")
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def single_shift_prob(n, d, k):
    """
    p1 = C(n-k, d) / C(n, d)
    表示某一个固定移位满足 pattern 的概率
    """
    if d > n - k:
        return 0.0
    return math.exp(log_binom(n - k, d) - log_binom(n, d))


def independent_approx_prob(n, d, k):
    """
    忽略不同移位之间相关性时的近似：
    1 - (1 - p1)^n
    """
    p1 = single_shift_prob(n, d, k)
    return 1.0 - (1.0 - p1) ** n


# =========================
# pattern 构造
# =========================
def make_pattern(n, k, kind="contiguous", rng=None):
    """
    返回 pattern 位置集合 P（numpy 数组）
    kind:
      - "contiguous": [0,1,...,k-1]
      - "random": 随机选 k 个位置
    """
    if rng is None:
        rng = np.random.default_rng()

    if k < 0 or k > n:
        raise ValueError("需要 0 <= k <= n")

    if kind == "contiguous":
        return np.arange(k, dtype=np.int64)
    elif kind == "random":
        return np.sort(rng.choice(n, size=k, replace=False))
    else:
        raise ValueError("kind 必须是 'contiguous' 或 'random'")


# =========================
# 随机生成固定汉明重量 d 的 01 串
# 用支撑集表示
# =========================
def random_support(n, d, rng=None):
    if rng is None:
        rng = np.random.default_rng()
    return np.sort(rng.choice(n, size=d, replace=False))


def support_to_indicator(n, support):
    x = np.zeros(n, dtype=np.uint8)
    x[support] = 1
    return x


# =========================
# 判断一个 x 是否“成功”
# 成功：存在某个循环移位，使得 pattern 位置全为 0
# =========================
def is_success(x, pattern):
    """
    x: 0/1 numpy 数组，长度 n
    pattern: numpy 数组，表示必须为 0 的位置集合 P
    """
    n = len(x)
    shifts = np.arange(n, dtype=np.int64)[:, None]            # shape (n,1)
    idx = (shifts + pattern[None, :]) % n                     # shape (n,k)
    hits = x[idx].sum(axis=1)                                 # 每个移位后，pattern 上有多少个 1
    return np.any(hits == 0)


def count_good_shifts(x, pattern):
    """
    返回有多少个循环移位满足 pattern 位置全为 0
    """
    n = len(x)
    shifts = np.arange(n, dtype=np.int64)[:, None]
    idx = (shifts + pattern[None, :]) % n
    hits = x[idx].sum(axis=1)
    return np.sum(hits == 0)


# =========================
# Monte Carlo 模拟
# =========================
def monte_carlo_success_probability(n, d, k, trials=5000, pattern_kind="contiguous",
                                    resample_pattern=False, seed=None):
    """
    估计成功概率

    参数：
    - n, d, k: 模型参数
    - trials: 实验次数
    - pattern_kind:
        "contiguous" 或 "random"
    - resample_pattern:
        False: 整个实验固定一个 pattern
        True : 每次 trial 都重新随机生成一个 pattern（仅 random 时有意义）
    """
    rng = np.random.default_rng(seed)

    if not resample_pattern:
        pattern = make_pattern(n, k, kind=pattern_kind, rng=rng)

    success_cnt = 0
    avg_good_shifts = 0.0

    for _ in range(trials):
        if resample_pattern:
            pattern = make_pattern(n, k, kind=pattern_kind, rng=rng)

        supp = random_support(n, d, rng=rng)
        x = support_to_indicator(n, supp)

        g = count_good_shifts(x, pattern)
        avg_good_shifts += g
        if g > 0:
            success_cnt += 1

    return {
        "success_prob": success_cnt / trials,
        "avg_good_shifts": avg_good_shifts / trials,
    }


# =========================
# 扫描不同 k
# =========================
def sweep_k_values(n, d, k_values, trials=5000, pattern_kind="contiguous",
                   resample_pattern=False, seed=12345):
    mc_probs = []
    avg_good_shifts = []
    single_probs = []
    indep_probs = []

    rng = np.random.default_rng(seed)
    seeds = rng.integers(0, 10**9, size=len(k_values))

    for k, s in zip(k_values, seeds):
        res = monte_carlo_success_probability(
            n=n,
            d=d,
            k=k,
            trials=trials,
            pattern_kind=pattern_kind,
            resample_pattern=resample_pattern,
            seed=int(s),
        )
        mc_probs.append(res["success_prob"])
        avg_good_shifts.append(res["avg_good_shifts"])
        single_probs.append(single_shift_prob(n, d, k))
        indep_probs.append(independent_approx_prob(n, d, k))

    return {
        "k_values": np.array(k_values),
        "mc_probs": np.array(mc_probs),
        "avg_good_shifts": np.array(avg_good_shifts),
        "single_probs": np.array(single_probs),
        "indep_probs": np.array(indep_probs),
    }


# =========================
# 画图
# =========================
def plot_results(result, n, d, pattern_kind="contiguous"):
    k_values = result["k_values"]
    mc_probs = result["mc_probs"]
    single_probs = result["single_probs"]
    indep_probs = result["indep_probs"]
    avg_good_shifts = result["avg_good_shifts"]

    plt.figure(figsize=(8, 5))
    plt.plot(k_values, mc_probs, marker='o', label='Monte Carlo success probability')
    plt.plot(k_values, indep_probs, marker='s', linestyle='--', label='Independent approximation')
    plt.plot(k_values, single_probs, marker='^', linestyle=':', label='Single-shift probability')
    plt.xlabel("k (number of forced-zero positions in pattern)")
    plt.ylabel("Probability")
    plt.title(f"Success probability vs k  (n={n}, d={d}, pattern={pattern_kind})")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.show()

    plt.figure(figsize=(8, 5))
    plt.plot(k_values, avg_good_shifts, marker='o')
    plt.xlabel("k")
    plt.ylabel("Average number of good cyclic shifts")
    plt.title(f"Average number of good shifts vs k  (n={n}, d={d}, pattern={pattern_kind})")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()


# =========================
# 示例
# =========================
if __name__ == "__main__":
    n = 512
    d = 64
    k_values = list(range(5, 121, 5))

    # 1) 连续 pattern：P = {0,1,...,k-1}
    result_contig = sweep_k_values(
        n=n,
        d=d,
        k_values=k_values,
        trials=3000,
        pattern_kind="contiguous",
        resample_pattern=False,
        seed=2026,
    )
    plot_results(result_contig, n, d, pattern_kind="contiguous")

    # 2) 随机 pattern（每个 k 固定一个随机 pattern）
    result_rand_fixed = sweep_k_values(
        n=n,
        d=d,
        k_values=k_values,
        trials=3000,
        pattern_kind="random",
        resample_pattern=False,
        seed=2027,
    )
    plot_results(result_rand_fixed, n, d, pattern_kind="random-fixed")

    # 3) 随机 pattern（每次 trial 都随机一个 pattern）
    result_rand_resample = sweep_k_values(
        n=n,
        d=d,
        k_values=k_values,
        trials=3000,
        pattern_kind="random",
        resample_pattern=True,
        seed=2028,
    )
    plot_results(result_rand_resample, n, d, pattern_kind="random-resampled")

    # 打印部分结果
    print("k | MC(contiguous) | indep approx | single-shift")
    for i, k in enumerate(k_values[:10]):
        print(f"{k:2d} | {result_contig['mc_probs'][i]:.6f} | "
              f"{result_contig['indep_probs'][i]:.6f} | "
              f"{result_contig['single_probs'][i]:.6f}")