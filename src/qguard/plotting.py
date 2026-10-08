"""Static, publication-friendly SVG figures; no generated or illustrative result data."""

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .benchmark import summarize

COLORS = {
    "asap": "#577590",
    "conservative": "#9b7e46",
    "nominal": "#43aa8b",
    "scenario_mean": "#a37acc",
    "robust": "#dd5362",
}


def plot_results(result, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update(
        {
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "svg.fonttype": "none",
            "svg.hashsalt": "qguard-v1",
        }
    )
    rows = summarize(result)
    if not rows:
        raise ValueError("No benchmark cases to plot")
    with (output / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4), constrained_layout=True)
    for method, color in COLORS.items():
        entries = [r for r in rows if r["method"] == method]
        for ax, metric in zip(axes, ("mean_fidelity", "mean_per_circuit_p10")):
            ax.plot(
                [r["sigma"] for r in entries],
                [r[metric] for r in entries],
                marker="o",
                label=method.replace("_", " "),
                color=color,
            )
            ax.set_xlabel("Held-out calibration dispersion (sigma)")
            ax.set_ylabel("Fidelity")
            ax.grid(alpha=0.2)
    axes[0].set_title("Mean fidelity (macro-average)")
    axes[1].set_title("Per-circuit 10th percentile (macro-average)")
    axes[0].legend(fontsize=8)
    fig.savefig(output / "robustness.svg", metadata={"Date": None})
    plt.close(fig)

    # Use a predefined family/first case; never select the most favorable result.
    case = next((c for c in result["cases"] if c["family"] == "qaoa"), result["cases"][0])
    fig, axes = plt.subplots(3, 1, figsize=(11, 6.5), constrained_layout=True, sharex=True)
    for ax, method in zip(axes, ("asap", "nominal", "robust")):
        starts = case["methods"][method]["schedule"]["starts"]
        for gate, start in zip(case["circuit"]["gates"], starts):
            for q in gate["qubits"]:
                ax.broken_barh(
                    [(start, gate["duration"])],
                    (q - 0.34, 0.68),
                    facecolors=COLORS[method],
                    edgecolors="white",
                    linewidth=0.6,
                )
                if gate["duration"] >= 4:
                    ax.text(
                        start + gate["duration"] / 2,
                        q,
                        gate["name"],
                        ha="center",
                        va="center",
                        fontsize=7,
                        color="white",
                    )
        ax.set_yticks(range(case["n"]), [f"q{q}" for q in range(case["n"])])
        ax.set_title(
            f"{method}: {case['methods'][method]['makespan_ticks']} ticks", loc="left", fontsize=10
        )
        ax.grid(axis="x", alpha=0.2)
    axes[-1].set_xlabel(f"Time (ticks; 1 tick = {case['device']['tick_ns']:g} ns)")
    fig.suptitle(f"Predefined example: {case['name']}", fontsize=12)
    fig.savefig(output / "schedule.svg", metadata={"Date": None})
    plt.close(fig)

    sigma = str(result["config"]["test_sigmas"][-1])
    fig, ax = plt.subplots(figsize=(9.5, 4.5), constrained_layout=True)
    labels, means, tails = [], [], []
    for case in result["cases"]:
        nominal = np.asarray(case["methods"]["nominal"]["evaluation"][sigma]["fidelities"])
        robust = np.asarray(case["methods"]["robust"]["evaluation"][sigma]["fidelities"])
        labels.append(f"{case['family']}\nn{case['n']}/s{case['circuit_seed']}")
        means.append(100 * float(np.mean(robust - nominal)))
        tails.append(100 * (float(np.quantile(robust, 0.1)) - float(np.quantile(nominal, 0.1))))
    x = np.arange(len(labels))
    ax.bar(x - 0.18, means, 0.36, color=COLORS["nominal"], label="Mean difference")
    ax.bar(x + 0.18, tails, 0.36, color=COLORS["robust"], label="10th-percentile difference")
    ax.axhline(0, color="#333333", lw=0.7)
    ax.set_xticks(x, labels, fontsize=7)
    ax.set_ylabel("Robust - nominal fidelity (percentage points)")
    ax.set_title(f"All workloads, held-out dispersion sigma={sigma}")
    ax.legend(fontsize=8)
    fig.savefig(output / "per_circuit.svg", metadata={"Date": None})
    plt.close(fig)
