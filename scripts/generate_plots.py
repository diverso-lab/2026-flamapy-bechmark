"""
generate_plots.py — Flamapy Benchmark Journal Plots
====================================================
Generates all 8 figures for the journal paper comparing
PySAT, BDD, Z3, and FaMA solvers on UVL feature models.

Usage:
    python generate_plots.py [--csv results/flamapy_benchmark_2026.csv] [--out plots/]
"""

import argparse
import os
import warnings
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
import pandas as pd
import seaborn as sns

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Style
# ---------------------------------------------------------------------------
SOLVER_ORDER   = ["pysat", "bdd", "z3", "fama"]
SOLVER_LABELS  = {"pysat": "PySAT", "bdd": "BDD", "z3": "Z3", "fama": "FaMA"}
SOLVER_PALETTE = {"pysat": "#4C72B0", "bdd": "#DD8452", "z3": "#55A868", "fama": "#C44E52"}

VARIANT_ORDER = ["glucose3", "glucose4", "minisat22", "lingeling", "maplesat", "cadical153"]

STATUS_COLORS = {"success": "#55A868", "timeout": "#FFC107", "error": "#C44E52"}

# Operations that are internal / not analysis operations
INTERNAL_OPS = {"__transform__", "__load__", "load", "__skipped_lossy__"}

# Operations shared by pysat + bdd + z3 (for cross-solver comparisons)
SHARED_OPS = {"Satisfiable", "CoreFeatures", "DeadFeatures", "FalseOptionalFeatures", "ConfigurationsNumber"}

sns.set_theme(style="whitegrid", font_scale=1.15)
plt.rcParams.update({
    "figure.dpi": 150,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "font.family": "sans-serif",
})

# ---------------------------------------------------------------------------
# Load & clean data
# ---------------------------------------------------------------------------

def load_data(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    # Drop internal/load operations for analysis
    df = df[~df["operation"].isin(INTERNAL_OPS)].copy()
    # Drop the single bad row with num_features < 0
    df = df[df["num_features"] > 0].copy()
    df["time_seconds"] = pd.to_numeric(df["time_seconds"], errors="coerce")
    df["num_features"] = pd.to_numeric(df["num_features"], errors="coerce")
    df["num_constraints"] = pd.to_numeric(df["num_constraints"], errors="coerce")
    return df


# ---------------------------------------------------------------------------
# Figure 1 — Success/Timeout/Error rate per solver (stacked bar)
# ---------------------------------------------------------------------------

def plot_status_rates(df: pd.DataFrame, out: Path) -> None:
    rows = []
    for solver in SOLVER_ORDER:
        sub = df[df["solver"] == solver]
        total = len(sub)
        if total == 0:
            continue
        for status in ["success", "timeout", "error"]:
            count = (sub["status"] == status).sum()
            rows.append({"solver": SOLVER_LABELS[solver], "status": status,
                         "pct": 100 * count / total, "count": count})
    data = pd.DataFrame(rows)

    fig, ax = plt.subplots(figsize=(8, 5))
    bottoms = np.zeros(len(SOLVER_ORDER))
    valid_solvers = [SOLVER_LABELS[s] for s in SOLVER_ORDER if s in df["solver"].unique()]
    x = np.arange(len(valid_solvers))

    for status in ["success", "timeout", "error"]:
        vals = []
        for sl in valid_solvers:
            row = data[(data["solver"] == sl) & (data["status"] == status)]
            vals.append(row["pct"].values[0] if len(row) else 0.0)
        bars = ax.bar(x, vals, bottom=bottoms, label=status.capitalize(),
                      color=STATUS_COLORS[status], edgecolor="white", linewidth=0.5)
        # Annotate segments > 3%
        for i, (v, b) in enumerate(zip(vals, bottoms)):
            if v > 3:
                ax.text(i, b + v / 2, f"{v:.1f}%", ha="center", va="center",
                        fontsize=9, color="white", fontweight="bold")
        bottoms += np.array(vals)

    ax.set_xticks(x)
    ax.set_xticklabels(valid_solvers)
    ax.set_ylabel("Percentage of solver calls (%)")
    ax.set_ylim(0, 105)
    ax.set_title("Figure 1 — Solver Call Outcome Rates")
    ax.legend(loc="upper right", framealpha=0.85)
    fig.tight_layout()
    fig.savefig(out / "fig1_status_rates.pdf")
    fig.savefig(out / "fig1_status_rates.png")
    plt.close(fig)
    print("  [1] fig1_status_rates saved")


# ---------------------------------------------------------------------------
# Figure 2 — Execution time distribution per solver (violin + box)
# ---------------------------------------------------------------------------

def plot_time_distributions(df: pd.DataFrame, out: Path) -> None:
    success = df[df["status"] == "success"].copy()
    success["log_time"] = np.log10(success["time_seconds"].clip(lower=1e-6))
    success["Solver"] = success["solver"].map(SOLVER_LABELS)

    order = [SOLVER_LABELS[s] for s in SOLVER_ORDER if s in success["solver"].unique()]
    palette = {SOLVER_LABELS[s]: SOLVER_PALETTE[s] for s in SOLVER_ORDER}

    fig, ax = plt.subplots(figsize=(9, 5))
    sns.violinplot(data=success, x="Solver", y="log_time", order=order,
                   palette=palette, inner="box", linewidth=0.8,
                   cut=0, ax=ax)

    # Y-axis: convert log ticks back to seconds labels
    yticks = [-6, -5, -4, -3, -2, -1, 0, 1, 2]
    ylabels = ["1µs", "10µs", "100µs", "1ms", "10ms", "100ms", "1s", "10s", "100s"]
    ax.set_yticks([y for y in yticks if ax.get_ylim()[0] <= y <= ax.get_ylim()[1]])
    ax.set_yticklabels([l for y, l in zip(yticks, ylabels)
                        if ax.get_ylim()[0] <= y <= ax.get_ylim()[1]])
    ax.set_ylabel("Execution time (log scale)")
    ax.set_xlabel("")
    ax.set_title("Figure 2 — Execution Time Distribution (successful runs)")
    fig.tight_layout()
    fig.savefig(out / "fig2_time_distributions.pdf")
    fig.savefig(out / "fig2_time_distributions.png")
    plt.close(fig)
    print("  [2] fig2_time_distributions saved")


# ---------------------------------------------------------------------------
# Figure 3 — Timeout rate heatmap (Solver × Operation)
# ---------------------------------------------------------------------------

def plot_timeout_heatmap(df: pd.DataFrame, out: Path) -> None:
    pivot_rows = []
    for solver in SOLVER_ORDER:
        sub = df[df["solver"] == solver]
        for op in sub["operation"].unique():
            op_sub = sub[sub["operation"] == op]
            total = len(op_sub)
            timeouts = (op_sub["status"] == "timeout").sum()
            pivot_rows.append({
                "Solver": SOLVER_LABELS[solver],
                "Operation": op,
                "Timeout %": 100 * timeouts / total if total else 0,
                "Total": total,
            })
    data = pd.DataFrame(pivot_rows)
    # Keep only operations with at least one timeout anywhere
    ops_with_timeouts = data[data["Timeout %"] > 0]["Operation"].unique()
    data = data[data["Operation"].isin(ops_with_timeouts)]
    pivot = data.pivot(index="Solver", columns="Operation", values="Timeout %").fillna(0)
    # Reorder rows
    pivot = pivot.reindex([SOLVER_LABELS[s] for s in SOLVER_ORDER if SOLVER_LABELS[s] in pivot.index])

    fig, ax = plt.subplots(figsize=(max(10, len(pivot.columns) * 1.1), 4))
    sns.heatmap(pivot, annot=True, fmt=".1f", cmap="YlOrRd", vmin=0, vmax=100,
                linewidths=0.4, linecolor="white",
                cbar_kws={"label": "Timeout rate (%)"}, ax=ax)
    ax.set_title("Figure 3 — Timeout Rate by Solver × Operation (%)")
    ax.set_xlabel("")
    ax.set_ylabel("")
    plt.xticks(rotation=35, ha="right")
    fig.tight_layout()
    fig.savefig(out / "fig3_timeout_heatmap.pdf")
    fig.savefig(out / "fig3_timeout_heatmap.png")
    plt.close(fig)
    print("  [3] fig3_timeout_heatmap saved")


# ---------------------------------------------------------------------------
# Figure 4 — Scalability: time vs. num_features (faceted scatter)
# ---------------------------------------------------------------------------

def plot_scalability(df: pd.DataFrame, out: Path) -> None:
    success = df[(df["status"] == "success") & df["num_features"].notna()].copy()
    # Focus on shared operations only for clean comparison
    success = success[success["operation"].isin(SHARED_OPS)].copy()
    # Collapse pysat variants: use median per (model, operation)
    pysat_agg = (success[success["solver"] == "pysat"]
                 .groupby(["model_name", "num_features", "operation"], as_index=False)["time_seconds"]
                 .median())
    pysat_agg["solver"] = "pysat"
    other = success[success["solver"] != "pysat"]
    combined = pd.concat([pysat_agg, other[["model_name", "num_features", "operation", "solver", "time_seconds"]]])
    combined["Solver"] = combined["solver"].map(SOLVER_LABELS)
    combined["log_time"] = np.log10(combined["time_seconds"].clip(lower=1e-6))

    ops = sorted(combined["operation"].unique())
    n_ops = len(ops)
    ncols = 3
    nrows = (n_ops + ncols - 1) // ncols

    fig, axes = plt.subplots(nrows, ncols, figsize=(5 * ncols, 4 * nrows), sharex=False)
    axes_flat = axes.flatten() if n_ops > 1 else [axes]
    palette = {SOLVER_LABELS[s]: SOLVER_PALETTE[s] for s in SOLVER_ORDER}

    for i, op in enumerate(ops):
        ax = axes_flat[i]
        sub = combined[combined["operation"] == op]
        for solver_label in [SOLVER_LABELS[s] for s in SOLVER_ORDER]:
            s_sub = sub[sub["Solver"] == solver_label]
            if s_sub.empty:
                continue
            ax.scatter(s_sub["num_features"], s_sub["log_time"],
                       color=palette[solver_label], alpha=0.25, s=8, label=solver_label)
            # Trend line (linear regression in log-space)
            x = s_sub["num_features"].values
            y = s_sub["log_time"].values
            if len(x) > 10:
                m, b = np.polyfit(x, y, 1)
                xs = np.linspace(x.min(), x.max(), 100)
                ax.plot(xs, m * xs + b, color=palette[solver_label], linewidth=1.5)

        ax.set_title(op, fontsize=10)
        ax.set_xlabel("# features")
        yticks = [-4, -3, -2, -1, 0, 1]
        ylabels = ["0.1ms", "1ms", "10ms", "100ms", "1s", "10s"]
        present = [(y, l) for y, l in zip(yticks, ylabels)
                   if ax.get_ylim()[0] - 0.5 <= y <= ax.get_ylim()[1] + 0.5]
        ax.set_yticks([y for y, _ in present])
        ax.set_yticklabels([l for _, l in present], fontsize=8)
        ax.set_ylabel("Time (log)")

    # Legend on last used axis
    handles = [plt.Line2D([0], [0], color=palette[SOLVER_LABELS[s]], lw=2, label=SOLVER_LABELS[s])
               for s in SOLVER_ORDER if SOLVER_LABELS[s] in combined["Solver"].unique()]
    axes_flat[0].legend(handles=handles, fontsize=8, loc="upper left")

    # Hide unused panels
    for j in range(n_ops, len(axes_flat)):
        axes_flat[j].set_visible(False)

    fig.suptitle("Figure 4 — Scalability: Execution Time vs. Model Size", fontsize=13, y=1.01)
    fig.tight_layout()
    fig.savefig(out / "fig4_scalability.pdf")
    fig.savefig(out / "fig4_scalability.png")
    plt.close(fig)
    print("  [4] fig4_scalability saved")


# ---------------------------------------------------------------------------
# Figure 5 — Solver coverage curve (% solved within time budget)
# ---------------------------------------------------------------------------

def plot_coverage_curve(df: pd.DataFrame, out: Path) -> None:
    # One (model, operation) task per solver; timeout rows get time = timeout_value (60s)
    TIMEOUT_VAL = 60.0
    tasks = df.copy()
    tasks["effective_time"] = tasks.apply(
        lambda r: r["time_seconds"] if r["status"] == "success" else
                  (TIMEOUT_VAL if r["status"] == "timeout" else np.nan),
        axis=1
    )
    tasks = tasks.dropna(subset=["effective_time"])

    thresholds = np.logspace(-4, np.log10(TIMEOUT_VAL), 400)

    fig, ax = plt.subplots(figsize=(9, 5))
    for solver in SOLVER_ORDER:
        sub = tasks[tasks["solver"] == solver]
        if sub.empty:
            continue
        total = len(sub)
        cov = [(sub["effective_time"] <= t).sum() / total * 100 for t in thresholds]
        ax.plot(thresholds, cov, label=SOLVER_LABELS[solver],
                color=SOLVER_PALETTE[solver], linewidth=2)

    ax.set_xscale("log")
    ax.set_xlabel("Time budget (seconds, log scale)")
    ax.set_ylabel("% of tasks completed")
    ax.set_ylim(0, 105)
    ax.set_title("Figure 5 — Solver Coverage Curve")
    ax.legend(loc="upper left", framealpha=0.85)

    # Reference lines
    for t, label in [(1, "1s"), (10, "10s"), (60, "60s")]:
        ax.axvline(t, color="gray", linestyle="--", linewidth=0.8, alpha=0.6)
        ax.text(t * 1.05, 5, label, color="gray", fontsize=8)

    fig.tight_layout()
    fig.savefig(out / "fig5_coverage_curve.pdf")
    fig.savefig(out / "fig5_coverage_curve.png")
    plt.close(fig)
    print("  [5] fig5_coverage_curve saved")


# ---------------------------------------------------------------------------
# Figure 6 — PySAT backend comparison (violin per variant)
# ---------------------------------------------------------------------------

def plot_pysat_variants(df: pd.DataFrame, out: Path) -> None:
    pysat = df[(df["solver"] == "pysat") & (df["status"] == "success")].copy()
    pysat["log_time"] = np.log10(pysat["time_seconds"].clip(lower=1e-6))
    order = [v for v in VARIANT_ORDER if v in pysat["solver_variant"].unique()]

    fig, ax = plt.subplots(figsize=(10, 5))
    sns.violinplot(data=pysat, x="solver_variant", y="log_time", order=order,
                   palette="Set2", inner="box", linewidth=0.8, cut=0, ax=ax)

    yticks = [-5, -4, -3, -2, -1, 0, 1]
    ylabels = ["10µs", "100µs", "1ms", "10ms", "100ms", "1s", "10s"]
    ax.set_yticks([y for y in yticks if ax.get_ylim()[0] <= y <= ax.get_ylim()[1]])
    ax.set_yticklabels([l for y, l in zip(yticks, ylabels)
                        if ax.get_ylim()[0] <= y <= ax.get_ylim()[1]])
    ax.set_ylabel("Execution time (log scale)")
    ax.set_xlabel("SAT solver backend")
    ax.set_title("Figure 6 — PySAT Backend Comparison (successful runs)")
    fig.tight_layout()
    fig.savefig(out / "fig6_pysat_variants.pdf")
    fig.savefig(out / "fig6_pysat_variants.png")
    plt.close(fig)
    print("  [6] fig6_pysat_variants saved")


# ---------------------------------------------------------------------------
# Figure 7 — Dataset complexity distribution
# ---------------------------------------------------------------------------

def plot_dataset_distribution(df: pd.DataFrame, out: Path) -> None:
    # One row per model (deduplicate)
    models = df[["model_name", "num_features", "num_constraints"]].drop_duplicates("model_name")

    fig, axes = plt.subplots(1, 2, figsize=(11, 4))

    for ax, col, label, color in [
        (axes[0], "num_features", "Number of features", "#4C72B0"),
        (axes[1], "num_constraints", "Number of cross-tree constraints", "#DD8452"),
    ]:
        vals = models[col].dropna().values
        vals = vals[vals > 0]  # filter out -1 sentinels and zeros (invalid for log scale)
        bins = np.logspace(np.log10(vals.min()), np.log10(vals.max() + 1), 35)
        ax.hist(vals, bins=bins, color=color, edgecolor="white", linewidth=0.5)
        ax.set_xscale("log")
        ax.set_xlabel(label)
        ax.set_ylabel("Number of models")
        ax.xaxis.set_major_formatter(ticker.ScalarFormatter())
        # Annotate median (only if median is positive, i.e., valid on log scale)
        med = np.median(vals)
        if med > 0:
            ax.axvline(med, color="crimson", linestyle="--", linewidth=1.2)
            ax.text(med * 1.1, ax.get_ylim()[1] * 0.88, f"median={med:.0f}",
                    color="crimson", fontsize=9)

    fig.suptitle("Figure 7 — Benchmark Dataset Complexity Distribution (1 382 models)", fontsize=12)
    fig.tight_layout()
    fig.savefig(out / "fig7_dataset_distribution.pdf", bbox_inches="tight")
    fig.savefig(out / "fig7_dataset_distribution.png", bbox_inches="tight")
    plt.close(fig)
    print("  [7] fig7_dataset_distribution saved")


# ---------------------------------------------------------------------------
# Figure 8 — Per-operation solver ranking (grouped bar, median time)
# ---------------------------------------------------------------------------

def plot_operation_ranking(df: pd.DataFrame, out: Path) -> None:
    success = df[(df["status"] == "success") & df["operation"].isin(SHARED_OPS)].copy()
    # Collapse pysat variants to median per (model, operation)
    pysat_agg = (success[success["solver"] == "pysat"]
                 .groupby(["model_name", "operation"], as_index=False)["time_seconds"]
                 .median())
    pysat_agg["solver"] = "pysat"
    other = success[success["solver"] != "pysat"][["model_name", "operation", "solver", "time_seconds"]]
    combined = pd.concat([pysat_agg, other])

    summary = (combined
               .groupby(["operation", "solver"])["time_seconds"]
               .median()
               .reset_index()
               .rename(columns={"time_seconds": "median_time"}))
    summary["Solver"] = summary["solver"].map(SOLVER_LABELS)
    summary["log_median"] = np.log10(summary["median_time"].clip(lower=1e-6))

    ops = sorted(SHARED_OPS)
    n_ops = len(ops)
    palette = {SOLVER_LABELS[s]: SOLVER_PALETTE[s] for s in SOLVER_ORDER}
    solver_labels = [SOLVER_LABELS[s] for s in SOLVER_ORDER]

    fig, ax = plt.subplots(figsize=(12, 5))
    x = np.arange(n_ops)
    n_solvers = len(SOLVER_ORDER)
    width = 0.18
    offsets = np.linspace(-(n_solvers - 1) / 2, (n_solvers - 1) / 2, n_solvers) * width

    for i, (solver, offset) in enumerate(zip(SOLVER_ORDER, offsets)):
        label = SOLVER_LABELS[solver]
        vals = []
        for op in ops:
            row = summary[(summary["operation"] == op) & (summary["solver"] == solver)]
            vals.append(row["log_median"].values[0] if len(row) else np.nan)
        bars = ax.bar(x + offset, vals, width=width * 0.9,
                      label=label, color=palette[label], edgecolor="white", linewidth=0.4)

    ax.set_xticks(x)
    ax.set_xticklabels(ops, rotation=20, ha="right")
    ax.set_ylabel("Median execution time (log scale)")
    yticks = [-4, -3, -2, -1, 0, 1]
    ylabels = ["0.1ms", "1ms", "10ms", "100ms", "1s", "10s"]
    ax.set_yticks([y for y in yticks if ax.get_ylim()[0] - 0.2 <= y <= ax.get_ylim()[1] + 0.2])
    ax.set_yticklabels([l for y, l in zip(yticks, ylabels)
                        if ax.get_ylim()[0] - 0.2 <= y <= ax.get_ylim()[1] + 0.2])
    ax.set_title("Figure 8 — Per-Operation Solver Ranking (median time, shared operations)")
    ax.legend(loc="upper left", framealpha=0.85)
    fig.tight_layout()
    fig.savefig(out / "fig8_operation_ranking.pdf")
    fig.savefig(out / "fig8_operation_ranking.png")
    plt.close(fig)
    print("  [8] fig8_operation_ranking saved")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate benchmark journal plots")
    parser.add_argument("--csv", default="results/flamapy_benchmark_2026.csv",
                        help="Path to benchmark CSV")
    parser.add_argument("--out", default="plots", help="Output directory")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print(f"Loading {args.csv} ...")
    df = load_data(args.csv)
    print(f"  {len(df):,} rows | {df['model_name'].nunique()} models | "
          f"{df['solver'].nunique()} solvers\n")

    print("Generating plots:")
    plot_status_rates(df, out)
    plot_time_distributions(df, out)
    plot_timeout_heatmap(df, out)
    plot_scalability(df, out)
    plot_coverage_curve(df, out)
    plot_pysat_variants(df, out)
    plot_dataset_distribution(df, out)
    plot_operation_ranking(df, out)

    print(f"\nDone — {len(list(out.glob('*.pdf')))} PDFs + PNGs written to '{out}/'")


if __name__ == "__main__":
    main()
