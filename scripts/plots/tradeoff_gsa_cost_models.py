import re
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt


# ============================================================
# Input logs
# ============================================================

LOG_FILES = {
    "MATZOV": Path("modified_DAWN_beta512_gsa_matzov.txt"),
    "BDGL16": Path("modified_DAWN_beta512_gsa_BDGL.txt"),
    "CoreSVP": Path("modified_DAWN_beta512_gsa_CoreSVP.txt"),
}


# ============================================================
# Parse estimator output
# ============================================================

def parse_log(path: Path, model_name: str) -> pd.DataFrame:
    """
    Parse lines like:
    [kappa-search] shape=gsa, cost=MATZOV, k_pat= 52, ..., cost=127.9064

    Extract:
        k_pat
        bit_security = final cost value
    """
    rows = []

    text = path.read_text(encoding="utf-8", errors="ignore")

    for line in text.splitlines():
        if "[kappa-search]" not in line:
            continue

        k_match = re.search(r"k_pat=\s*(\d+)", line)
        cost_matches = re.findall(r"\bcost=([0-9]+(?:\.[0-9]+)?)", line)

        if k_match and cost_matches:
            k_pat = int(k_match.group(1))
            bit_security = float(cost_matches[-1])
            rows.append({
                "model": model_name,
                "k_pat": k_pat,
                "bit_security": bit_security,
            })

    return pd.DataFrame(rows)


dfs = []
for model_name, path in LOG_FILES.items():
    df = parse_log(path, model_name)
    dfs.append(df)

data = pd.concat(dfs, ignore_index=True)


# ============================================================
# Plot
# ============================================================

plt.figure(figsize=(8.5, 5.5))

markers = {
    "MATZOV": "o",
    "BDGL16": "s",
    "CoreSVP": "^",
}


text_styles = {
    "MATZOV": {"offset": (-105, -12), "ha": "left"},
    "BDGL16": {"offset": (5, 10), "ha": "left"},
    "CoreSVP": {"offset": (5, 10), "ha": "left"},
}

y_min = float(data["bit_security"].min())
y_max = float(data["bit_security"].max())
y_bottom = y_min - 1.5
y_top = y_max + 2.0

for model_name in ["MATZOV", "BDGL16", "CoreSVP"]:
    df = data[data["model"] == model_name].sort_values("k_pat")

    x = df["k_pat"].to_numpy()
    y = df["bit_security"].to_numpy()

    plt.plot(
        x,
        y,
        marker=markers[model_name],
        markevery=4,
        markersize=4.5,
        linewidth=2,
        label=model_name,
    )


    min_idx = df["bit_security"].idxmin()
    min_row = df.loc[min_idx]

    min_k = int(min_row["k_pat"])
    min_sec = float(min_row["bit_security"])


    plt.scatter(
        min_k,
        min_sec,
        s=45,
        marker=markers[model_name],
        zorder=5,
    )


    plt.vlines(
        x=min_k,
        ymin=y_bottom,
        ymax=min_sec,
        linestyles="--",
        linewidth=1.2,
        alpha=0.9,
    )

    style = text_styles[model_name]
    dx, dy = style["offset"]

    plt.annotate(
        rf"min: $k_{{\mathrm{{pat}}}}={min_k}$, {min_sec:.1f}",
        xy=(min_k, min_sec),
        xytext=(dx, dy),
        textcoords="offset points",
        fontsize=9,
        ha=style["ha"],
    )


# ============================================================
# Axis style
# ============================================================

plt.ylim(y_bottom, y_top)

plt.xlabel(r"pattern length $k_{\mathrm{pat}}$")
plt.ylabel("bit security")
plt.title("Tradeoff between pattern length and security under GSA")

plt.grid(True, alpha=0.35)
plt.legend(loc="upper right")

plt.tight_layout()

output_name = "tradeoff_gsa_cost_models.png"
plt.savefig(output_name, dpi=300, bbox_inches="tight")
plt.show()
plt.close()

print(f"Saved figure to: {output_name}")


# ============================================================
# Print minima
# ============================================================

print("\nMinimum points:")
for model_name in ["MATZOV", "BDGL16", "CoreSVP"]:
    df = data[data["model"] == model_name]
    row = df.loc[df["bit_security"].idxmin()]
    print(
        f"{model_name:8s}: "
        f"k_pat = {int(row['k_pat'])}, "
        f"bit security = {row['bit_security']:.1f}"
    )