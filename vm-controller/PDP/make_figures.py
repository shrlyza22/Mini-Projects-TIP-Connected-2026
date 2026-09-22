#!/usr/bin/env python3
"""
Figure generator for the ZTNA paper revision (ICCEREC 2026).

Produces, in the current directory:
  fig_sensitivity_heatmap.png  trust score over the weight simplex (LIMITED case)
  fig_latency.png              provisioning/revocation latency (from perf_results.csv)
  fig_robustness.png           classification-preservation rates per domain

Requirements: matplotlib (pip install matplotlib, or sudo apt-get install -y python3-matplotlib).
It reuses sensitivity_analysis.py for the model-level figures.

Run (on VM1, in the PDP folder):
    cd ~/PDP
    python3 make_figures.py
    python3 make_figures.py --src perf_results.csv
"""

import argparse
import csv
import os

import sensitivity_analysis as sa


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


# ---------------------------------------------------------------------------
def heatmap(path="fig_sensitivity_heatmap.png", step=0.02):
    """Trust score for the LIMITED boundary case over the weight simplex."""
    plt = _plt()
    R, C, B = sa.CASES[1][1:]  # the LIMITED case (most boundary-sensitive)

    xs, ys, zs = [], [], []
    n = int(round(1 / step))
    for i in range(n + 1):
        for j in range(n + 1 - i):
            k = n - i - j
            wR, wC, wB = i / n, j / n, k / n
            xs.append(wR)
            ys.append(wC)
            zs.append(sa.trust((wR, wC, wB), R, C, B))

    fig, ax = plt.subplots(figsize=(7, 5.5))
    sc = ax.scatter(xs, ys, c=zs, cmap="viridis", s=18)
    ax.scatter([sa.BASELINE_WEIGHTS[0]], [sa.BASELINE_WEIGHTS[1]],
               facecolors="none", edgecolors="red", s=180, linewidths=2,
               label="Baseline 0.5/0.3/0.2")
    contours = ax.tricontour(xs, ys, zs, levels=[40, 70], colors="white")
    ax.clabel(contours, fmt="T=%d")
    ax.set_xlabel("$w_R$ (identity)")
    ax.set_ylabel("$w_C$ (context)")
    ax.set_title(f"Trust score for case {sa.CASES[1][0]} (R={R}, C={C}, B={B})")
    ax.legend(loc="upper right")
    fig.colorbar(sc, ax=ax, label="$T$")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    print("saved", path)


# ---------------------------------------------------------------------------
def _read_perf_stats(csv_path):
    """Return {operation: {n, mean, p50, p95, min, max}} from perf_results.csv."""
    stats = {}
    if not os.path.exists(csv_path):
        return stats
    with open(csv_path, newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    in_stats = False
    for row in rows:
        if not row:
            continue
        if row[0] == "operation":
            in_stats = True
            continue
        if in_stats and row[0] in ("provision", "revoke"):
            stats[row[0]] = {
                "n": int(row[1]), "mean": float(row[2]),
                "p50": float(row[3]), "p95": float(row[4]),
                "min": float(row[5]), "max": float(row[6]),
            }
    return stats


def latency_bars(src="perf_results.csv", path="fig_latency.png"):
    plt = _plt()
    stats = _read_perf_stats(src)
    if not stats:
        print(f"skip latency figure: {src} not found or empty")
        return

    labels = ["Provisioning\n(login)", "Revocation\n(logout)"]
    metrics = ["mean", "p50", "p95"]
    keys = ["provision", "revoke"]
    width = 0.25
    positions = range(len(keys))

    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    for idx, metric in enumerate(metrics):
        values = [stats[k][metric] for k in keys]
        offset = (idx - 1) * width
        bars = ax.bar([p + offset for p in positions], values, width,
                      label=metric)
        ax.bar_label(bars, fmt="%.0f", fontsize=8)
    ax.set_xticks(list(positions))
    ax.set_xticklabels(labels)
    ax.set_ylabel("Latency (ms)")
    ax.set_title("PDP flow provisioning and revocation latency")
    ax.legend(title="statistic")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    print("saved", path)


# ---------------------------------------------------------------------------
def robustness_bars(path="fig_robustness.png"):
    plt = _plt()
    simplex = sa.robustness_sweep(0.05)
    ordered = sa.robustness_restricted(0.05)
    local = sa.robustness_neighbourhood(0.05)
    labels = ["Weight\nsimplex", "Ordered domain\n$w_R\\geq w_C\\geq w_B$",
              "Local\n$\\pm0.05$"]
    values = [
        100 * simplex[0] / simplex[1],
        100 * ordered[0] / ordered[1],
        100 * local[0] / local[1],
    ]

    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    bars = ax.bar(labels, values, color=["#E85C8A", "#5FB4E8", "#2DD4A7"])
    ax.bar_label(bars, fmt="%.1f%%", fontsize=9)
    ax.set_ylabel("Classification preservation (%)")
    ax.set_ylim(0, 100)
    ax.set_title("Robustness of the FULL/LIMITED/DENIED mapping")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    print("saved", path)


# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Generate paper figures")
    parser.add_argument("--src", default="perf_results.csv",
                        help="perf results CSV for the latency figure")
    args = parser.parse_args()

    try:
        import matplotlib  # noqa: F401
    except ImportError:
        print("matplotlib not installed - run: sudo apt-get install -y python3-matplotlib")
        return 2

    heatmap()
    latency_bars(args.src)
    robustness_bars()
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
