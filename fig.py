import re
import zipfile
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt


# ============================================================
# Input
# ============================================================

ZIP_PATH = Path("dawn_beta512.zip")
# 如果你在 ChatGPT 环境里运行，可以改成：
# ZIP_PATH = Path("/mnt/data/dawn_beta512.zip")


# ============================================================
# Parse logs
# ============================================================

def parse_kappa_search_from_zip(zip_path: Path) -> pd.DataFrame:
    rows = []

    pattern = re.compile(
        r"\[kappa-search\]\s+"
        r"shape=(?P<shape>\w+),\s+"
        r"cost=(?P<cost>[A-Za-z0-9_]+),\s+"
        r"k_pat=\s*(?P<k_pat>\d+),\s+"
        r"best_kappa=\s*(?P<kappa>\d+)"
    )

    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if not name.endswith(".txt"):
                continue

            text = zf.read(name).decode("utf-8", errors="ignore")

            for line in text.splitlines():
                m = pattern.search(line)
                if m is None:
                    continue

                rows.append({
                    "file": name,
                    "shape": m.group("shape").lower(),
                    "cost": m.group("cost"),
                    "k_pat": int(m.group("k_pat")),
                    "kappa": int(m.group("kappa")),
                })

    df = pd.DataFrame(rows)

    if df.empty:
        raise ValueError("No [kappa-search] lines were found in the zip file.")

    return df


df = parse_kappa_search_from_zip(ZIP_PATH)


# ============================================================
# Plot: k_pat vs kappa
# ============================================================

shape_order = ["gsa", "zgsa"]
cost_order = ["MATZOV", "BDGL16", "CoreSVP"]

line_styles = {
    "gsa": "-",
    "zgsa": "--",
}

markers = {
    "MATZOV": "o",
    "BDGL16": "s",
    "CoreSVP": "^",
}

plt.figure(figsize=(8.5, 5.2))

for shape in shape_order:
    for cost in cost_order:
        sub = df[(df["shape"] == shape) & (df["cost"] == cost)].copy()
        if sub.empty:
            continue

        sub = sub.sort_values("k_pat")

        x = sub["k_pat"].to_numpy()
        y = sub["kappa"].to_numpy()

        plt.plot(
            x,
            y,
            linestyle=line_styles[shape],
            marker=markers[cost],
            markevery=6,
            linewidth=1.8,
            markersize=4.5,
            label=f"{shape.upper()} + {cost}",
        )

plt.xlabel(r"Pattern length $k_{\mathrm{pat}}$")
plt.ylabel(r"Optimized tail-guessing dimension $\kappa$")
plt.title(r"DAWN-$\beta$-512: relation between $k_{\mathrm{pat}}$ and $\kappa$")
plt.grid(True, linestyle="--", alpha=0.35)
plt.legend(ncol=2, fontsize=9)
plt.tight_layout()

plt.savefig("dawn_beta512_pattern_kappa_relation.pdf", bbox_inches="tight")
plt.savefig("dawn_beta512_pattern_kappa_relation.png", dpi=300, bbox_inches="tight")

plt.show()