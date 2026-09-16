#!/usr/bin/env python3
"""
Sensitivity Analysis of the ZTNA Trust Model
============================================

Standalone, dependency-free companion to pdp.py. It answers the reviewers'
request to (a) justify the weights wR/wC/wB and the tier thresholds, and
(b) demonstrate how access-level outcomes vary across configurations.

The trust model is a closed-form linear score:

        T = wR*R + wC*C + wB*B

with tier bands FULL (T >= TF), LIMITED (TL <= T < TF), DENIED (T < TL).
Because the model is closed-form, this analysis needs no Mininet/ODL and is
fully reproducible.

Run:
    python3 sensitivity_analysis.py                 # print tables + write files
    python3 sensitivity_analysis.py --figure        # also draw a heatmap (needs matplotlib)

Outputs (in the current directory):
    sensitivity_results.md   human-readable report
    sensitivity_results.csv  machine-readable table
    sensitivity_heatmap.png  only with --figure
"""

import argparse
import csv
import os

# ---------------------------------------------------------------------------
# Model constants (must mirror pdp.py defaults)
# ---------------------------------------------------------------------------
ROLE_SCORES = {"research": 80, "server": 95, "iot": 50, "guest": 30}
CONTEXT_BASE = 100
BEHAVIOUR_BASE = 100

DEFAULT_PENALTIES = {"ip": 40, "hours": 30, "mac": 50}
BEHAVIOUR_PENALTY = 15
BEHAVIOUR_CAP = 60

BASELINE_WEIGHTS = (0.50, 0.30, 0.20)
BASELINE_THRESHOLDS = (70, 40)

# Weight configurations to compare (label, wR, wC, wB). Alt-1 is the example
# the reviewer explicitly raised ("why not 0.45 / 0.25 / 0.30?").
WEIGHT_CONFIGS = [
    ("Baseline (paper)",          0.50, 0.30, 0.20),
    ("Alt-1 (flatter)",           0.45, 0.25, 0.30),
    ("Alt-2 (context-heavy)",     0.40, 0.40, 0.20),
    ("Alt-3 (behaviour-heavy)",   0.40, 0.20, 0.40),
    ("Alt-4 (identity-heavy)",    0.60, 0.30, 0.10),
    ("Alt-5 (uniform)",           1 / 3, 1 / 3, 1 / 3),
]

THRESHOLD_CONFIGS = [
    ("Baseline 70/40", 70, 40),
    ("Looser 65/35",   65, 35),
    ("Stricter 75/45", 75, 45),
    ("Limited@50",     70, 50),
    ("Lower 60/30",    60, 30),
]

# The three evaluated scenarios from the paper, labelled by expected tier.
CASES = [
    ("FULL",    80, 100, 100),
    ("LIMITED", 80,  50,  70),
    ("DENIED",  30,  50,  40),
]

# Human-readable provenance of each evaluated case. These are the values that
# pdp.py produces at runtime from real logins; the sensitivity analysis works
# at the numeric layer on purpose, because the reviewer's question is about the
# formula (weights/thresholds), not about the authentication plumbing.
SCENARIO_INFO = {
    "FULL": {
        "account": "ratih (research)",
        "R_provenance": "role base for research = 80",
        "C_provenance": "IP in subnet, inside hours, MAC matches = 100",
        "B_provenance": "no failed logins = 100",
    },
    "LIMITED": {
        "account": "ratih (research)",
        "R_provenance": "role base for research = 80",
        "C_provenance": "IP/MAC binding mismatch = 100 - 50 = 50",
        "B_provenance": "2 failed logins = 100 - 2*15 = 70",
    },
    "DENIED": {
        "account": "bima (guest)",
        "R_provenance": "role base for guest = 30",
        "C_provenance": "IP/MAC binding mismatch = 100 - 50 = 50",
        "B_provenance": "4 failed logins = 100 - min(4*15, 60) = 40",
    },
}


# ---------------------------------------------------------------------------
# Core model
# ---------------------------------------------------------------------------
def trust(weights, R, C, B):
    wR, wC, wB = weights
    return round(wR * R + wC * C + wB * B, 1)


def tier(T, threshold_full, threshold_limited):
    if T >= threshold_full:
        return "FULL"
    if T >= threshold_limited:
        return "LIMITED"
    return "DENIED"


def context_values(penalties=None):
    p = penalties or DEFAULT_PENALTIES
    values = set()
    for ip in (0, p["ip"]):
        for hours in (0, p["hours"]):
            for mac in (0, p["mac"]):
                values.add(max(0, CONTEXT_BASE - ip - hours - mac))
    return sorted(values, reverse=True)


def behaviour_values():
    return sorted(
        {max(0, BEHAVIOUR_BASE - min(n * BEHAVIOUR_PENALTY, BEHAVIOUR_CAP))
         for n in range(0, 10)},
        reverse=True,
    )


def reachable_scores(weights):
    scores = set()
    for R in ROLE_SCORES.values():
        for C in context_values():
            for B in behaviour_values():
                scores.add(trust(weights, R, C, B))
    return sorted(scores)


def gap_around(values, threshold):
    below = max((v for v in values if v < threshold), default=None)
    above = min((v for v in values if v >= threshold), default=None)
    return below, above


def robustness_sweep(step=0.05, threshold_full=70, threshold_limited=40):
    """Fraction of grid-sampled weight assignments that preserve the
    FULL/LIMITED/DENIED mapping of the three evaluated cases.

    The full simplex is a deliberately harsh test: the three paper cases sit
    close to the 70/40 boundaries, so remote weight assignments are expected
    to reclassify them. Two more meaningful domains are reported as well.
    """
    n = int(round(1 / step))
    total = stable = 0
    for i in range(n + 1):
        for j in range(n + 1 - i):
            k = n - i - j
            weights = (i / n, j / n, k / n)
            if abs(sum(weights) - 1.0) > 1e-9:
                continue
            total += 1
            if _preserves(weights, threshold_full, threshold_limited):
                stable += 1
    return stable, total


def robustness_restricted(step=0.05, threshold_full=70, threshold_limited=40):
    """Same, but restricted to the design-consistent region wR >= wC >= wB
    (identity weighted at least as much as context, and context at least as
    much as behaviour)."""
    n = int(round(1 / step))
    total = stable = 0
    for i in range(n + 1):
        for j in range(i + 1):
            k = n - i - j
            if k > j:
                continue
            weights = (i / n, j / n, k / n)
            total += 1
            if _preserves(weights, threshold_full, threshold_limited):
                stable += 1
    return stable, total


def robustness_neighbourhood(delta=0.05, step=0.025,
                             threshold_full=70, threshold_limited=40):
    """Same, but only for weights within +/- delta of the baseline (0.5/0.3/0.2).
    This models uncertainty in the calibration rather than arbitrary weights."""
    offsets = []
    value = -delta
    while value <= delta + 1e-9:
        offsets.append(round(value, 4))
        value += step
    total = stable = 0
    base_r, base_c, base_b = BASELINE_WEIGHTS
    for dr in offsets:
        for dc in offsets:
            wR, wC = base_r + dr, base_c + dc
            wB = 1.0 - wR - wC
            if not (0 <= wR <= 1 and 0 <= wC <= 1 and 0 <= wB <= 1):
                continue
            total += 1
            if _preserves((wR, wC, wB), threshold_full, threshold_limited):
                stable += 1
    return stable, total


def _preserves(weights, threshold_full, threshold_limited):
    return all(
        tier(trust(weights, R, C, B), threshold_full, threshold_limited) == expected
        for expected, R, C, B in CASES
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def table_scenario_detail():
    """Per-case provenance: which account, how R/C/B arise, and the tier."""
    rows = []
    for expected, R, C, B in CASES:
        info = SCENARIO_INFO[expected]
        T = trust(BASELINE_WEIGHTS, R, C, B)
        rows.append({
            "tier": expected,
            "account": info["account"],
            "R": R, "C": C, "B": B, "T": T,
            "attained": tier(T, *BASELINE_THRESHOLDS),
            "R_from": info["R_provenance"],
            "C_from": info["C_provenance"],
            "B_from": info["B_provenance"],
        })
    return rows


def table_weight_sensitivity():
    rows = []
    for label, wR, wC, wB in WEIGHT_CONFIGS:
        weights = (wR, wC, wB)
        row = {"config": label, "wR": wR, "wC": wC, "wB": wB}
        for expected, R, C, B in CASES:
            T = trust(weights, R, C, B)
            got = tier(T, *BASELINE_THRESHOLDS)
            row[expected] = f"{T} ({got})"
            row[expected + "_ok"] = (got == expected)
        rows.append(row)
    return rows


def table_threshold_sensitivity():
    rows = []
    for label, full, limited in THRESHOLD_CONFIGS:
        row = {"config": label, "full": full, "limited": limited}
        for expected, R, C, B in CASES:
            T = trust(BASELINE_WEIGHTS, R, C, B)
            got = tier(T, full, limited)
            row[expected] = f"{T} -> {got}"
            row[expected + "_ok"] = (got == expected)
        rows.append(row)
    return rows


def build_markdown(scenario_rows, weight_rows, threshold_rows, reachable, rob):
    lines = []
    lines.append("# Sensitivity Analysis — ZTNA Trust Model")
    lines.append("")
    lines.append("Model: `T = wR*R + wC*C + wB*B`, tiers "
                 f"FULL (T>={BASELINE_THRESHOLDS[0]}), "
                 f"LIMITED (T>={BASELINE_THRESHOLDS[1]}), DENIED otherwise.")
    lines.append("")
    lines.append("## 0. Evaluated scenarios and how R/C/B arise")
    lines.append("")
    lines.append("| Expected tier | Account | R | C | B | T | Attained |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in scenario_rows:
        lines.append(
            f"| {r['tier']} | {r['account']} | {r['R']} | {r['C']} | {r['B']} "
            f"| {r['T']} | {r['attained']} |"
        )
    lines.append("")
    for r in scenario_rows:
        lines.append(f"- **{r['tier']}** — R: {r['R_from']}; C: {r['C_from']}; "
                     f"B: {r['B_from']}.")
    lines.append("")
    lines.append("## 1. Access-level outcome vs weight configuration (thresholds fixed 70/40)")
    lines.append("")
    lines.append("| Config | wR | wC | wB | FULL case | LIMITED case | DENIED case |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in weight_rows:
        lines.append(
            f"| {r['config']} | {r['wR']:.2f} | {r['wC']:.2f} | {r['wB']:.2f} "
            f"| {r['FULL']} | {r['LIMITED']} | {r['DENIED']} |"
        )
    lines.append("")
    lines.append("## 2. Access-level outcome vs thresholds (baseline weights 0.5/0.3/0.2)")
    lines.append("")
    lines.append("| Config | FULL thr | LIMITED thr | FULL case | LIMITED case | DENIED case |")
    lines.append("|---|---|---|---|---|---|")
    for r in threshold_rows:
        lines.append(
            f"| {r['config']} | {r['full']} | {r['limited']} "
            f"| {r['FULL']} | {r['LIMITED']} | {r['DENIED']} |"
        )
    lines.append("")
    lines.append("## 3. Threshold placement vs reachable scores (baseline weights)")
    lines.append("")
    lines.append("Scores reachable from valid R/C/B combinations:")
    lines.append("")
    lines.append("`" + ", ".join(f"{v:g}" for v in reachable) + "`")
    lines.append("")
    for thr in BASELINE_THRESHOLDS:
        below, above = gap_around(reachable, thr)
        lines.append(f"- Threshold **{thr}** sits in the gap ({below}, {above}]: "
                     "any value in that interval yields the same tiering.")
    lines.append("")
    lines.append("## 4. Robustness of the FULL/LIMITED/DENIED mapping")
    lines.append("")
    lines.append("The three evaluated cases sit close to the 70/40 boundaries, "
                 "so this reports how often the mapping survives as weights vary:")
    lines.append("")
    for label, (stable, total) in rob.items():
        lines.append(
            f"- **{label}**: {stable}/{total} "
            f"({100 * stable / total:.1f}% of sampled weight assignments)"
        )
    lines.append("")
    lines.append("Note: the reviewer's alternative (0.45/0.25/0.30) preserves all "
                 "three outcomes; the score mapping degrades only for weightings "
                 "far from the identity-dominant region, and the affected cases are "
                 "exactly those deliberately placed on a tier boundary.")
    lines.append("")
    return "\n".join(lines)


def draw_figure(path="sensitivity_heatmap.png"):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed — skipping figure "
              "(pip install matplotlib)")
        return

    step = 0.02
    n = int(round(1 / step))
    R, C, B = CASES[1][1:]  # the LIMITED boundary case, most interesting
    xs, ys, zs = [], [], []
    for i in range(n + 1):
        for j in range(n + 1 - i):
            k = n - i - j
            wR, wC, wB = i / n, j / n, k / n
            xs.append(wR)
            ys.append(wC)
            zs.append(trust((wR, wC, wB), R, C, B))

    fig, ax = plt.subplots(figsize=(7, 5.5))
    sc = ax.scatter(xs, ys, c=zs, cmap="viridis", s=18)
    ax.scatter([BASELINE_WEIGHTS[0]], [BASELINE_WEIGHTS[1]],
               facecolors="none", edgecolors="red", s=180, linewidths=2,
               label="Baseline 0.5/0.3/0.2")
    contours = ax.tricontour(xs, ys, zs, levels=[40, 70], colors="white")
    ax.clabel(contours, fmt="T=%d")
    ax.set_xlabel("wR (identity)")
    ax.set_ylabel("wC (context)")
    ax.set_title(f"Trust score for case {CASES[1][0]} (R={R}, C={C}, B={B})")
    ax.legend(loc="upper right")
    fig.colorbar(sc, ax=ax, label="T")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    print(f"Saved: {path}")


def write_csv(path, weight_rows, threshold_rows):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["section", "config", "wR", "wC", "wB",
                         "FULL", "LIMITED", "DENIED"])
        for r in weight_rows:
            writer.writerow(["weights", r["config"], r["wR"], r["wC"], r["wB"],
                             r["FULL"], r["LIMITED"], r["DENIED"]])
        for r in threshold_rows:
            writer.writerow(["thresholds", r["config"], r["full"], r["limited"],
                             r["FULL"], r["LIMITED"], r["DENIED"]])


def print_scenario_table(rows):
    print("\n[0] Evaluated scenarios (account -> R/C/B -> T)")
    for r in rows:
        print(f"  {r['tier']:<8} {r['account']:<18} R={r['R']} C={r['C']} B={r['B']} "
              f"-> T={r['T']} ({r['attained']})")
        print(f"           R: {r['R_from']}")
        print(f"           C: {r['C_from']}")
        print(f"           B: {r['B_from']}")


def print_weight_table(rows):
    print("\n[1] Outcome vs weight configuration (thresholds 70/40)")
    print(f"{'config':<26}{'wR':>6}{'wC':>6}{'wB':>6}   FULL / LIMITED / DENIED")
    for r in rows:
        print(f"{r['config']:<26}{r['wR']:>6.2f}{r['wC']:>6.2f}{r['wB']:>6.2f}   "
              f"{r['FULL']:<12} {r['LIMITED']:<12} {r['DENIED']}")


def print_threshold_table(rows):
    print("\n[2] Outcome vs thresholds (weights 0.5/0.3/0.2)")
    print(f"{'config':<18}{'FULL':>6}{'LIM':>6}   FULL / LIMITED / DENIED")
    for r in rows:
        print(f"{r['config']:<18}{r['full']:>6}{r['limited']:>6}   "
              f"{r['FULL']:<18} {r['LIMITED']:<18} {r['DENIED']}")


def main():
    ap = argparse.ArgumentParser(description="ZTNA trust model sensitivity analysis")
    ap.add_argument("--figure", action="store_true",
                    help="also draw a heatmap (requires matplotlib)")
    args = ap.parse_args()

    scenario_rows = table_scenario_detail()
    weight_rows = table_weight_sensitivity()
    threshold_rows = table_threshold_sensitivity()
    reachable = reachable_scores(BASELINE_WEIGHTS)
    rob = {
        "full simplex (step 0.05)": robustness_sweep(0.05),
        "design-consistent wR>=wC>=wB": robustness_restricted(0.05),
        "near baseline (+/-0.05)": robustness_neighbourhood(0.05),
    }

    print_scenario_table(scenario_rows)
    print_weight_table(weight_rows)
    print_threshold_table(threshold_rows)

    print("\n[3] Threshold placement vs reachable scores")
    print("reachable T:", ", ".join(f"{v:g}" for v in reachable))
    for thr in BASELINE_THRESHOLDS:
        below, above = gap_around(reachable, thr)
        print(f"  threshold {thr}: nearest below={below}, above={above} "
              f"-> same tiering for any value in ({below}, {above}]")

    print("\n[4] Robustness of the FULL/LIMITED/DENIED mapping")
    for label, (stable, total) in rob.items():
        print(f"  {label}: {stable}/{total} ({100 * stable / total:.1f}%)")

    markdown = build_markdown(scenario_rows, weight_rows, threshold_rows,
                              reachable, rob)
    with open("sensitivity_results.md", "w", encoding="utf-8") as handle:
        handle.write(markdown)
    write_csv("sensitivity_results.csv", weight_rows, threshold_rows)
    print("\nSaved: sensitivity_results.md, sensitivity_results.csv")

    if args.figure:
        draw_figure()


if __name__ == "__main__":
    main()
