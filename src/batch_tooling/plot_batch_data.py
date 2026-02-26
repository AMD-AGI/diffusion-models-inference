import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path
from typing import DefaultDict, List, Optional, Tuple

import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator

FORMULA = r"$\frac{A - B}{B} \times 100\%$"
FORMULA_BOX = dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor="gray", alpha=0.95)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Plot latency and throughput from results.csv.")
    p.add_argument("--input", type=str, required=True, help="Primary results CSV")
    p.add_argument("--output-dir", type=str, default=None, help="Output directory (default: input file dir)")
    p.add_argument("--compare", type=str, default=None, help="Second CSV to compare against")
    args = p.parse_args()
    return args


def load_csv(path: str) -> List[Tuple[str, int, float]]:
    """Load CSV produced by run.py using configs from generate_batch_configs.py. Expects metric=latency. Returns (model_base, batch_size, latency_sec) for rows with name ending .bsN."""
    rows, bs_re = [], re.compile(r"\.bs(\d+)$")
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not (m := bs_re.search(row.get("model", ""))):
                continue
            try:
                rows.append((row["model"][: m.start()], int(m.group(1)), float(row.get("performance", 0))))
            except ValueError:
                continue
    return rows


def group_by_model(rows: List[Tuple[str, int, float]]) -> DefaultDict[str, List[Tuple[int, float]]]:
    by_model: DefaultDict[str, List[Tuple[int, float]]] = defaultdict(list)
    for base, bs, lat in rows:
        by_model[base].append((bs, lat))
    for base in by_model:
        by_model[base].sort(key=lambda x: x[0])
    return by_model


def matching_comparisons(primary_base: str, compare_by_model: DefaultDict[str, List[Tuple[int, float]]]) -> List[Tuple[str, List[int], List[float]]]:
    """Return comparison series where name is primary_base + one or more extra dotted segments (e.g. flux.usp.hopper or flux2.fp8gemm.hopper.h100)."""
    primary_parts = primary_base.split(".")
    return [
        (comp_base, [p[0] for p in points], [p[1] for p in points])
        for comp_base, points in compare_by_model.items()
        if (comp_parts := comp_base.split(".")) and len(comp_parts) > len(primary_parts) and comp_parts[: len(primary_parts)] == primary_parts
    ]


def _pct_change(primary_bs: List[int], primary_lat: List[float], comp_bs: List[int], comp_lat: List[float]) -> Tuple[List[int], List[float], List[float]]:
    primary_by_bs = dict(zip(primary_bs, primary_lat))
    comp_by_bs = dict(zip(comp_bs, comp_lat))
    common_bs = sorted(set(primary_by_bs) & set(comp_by_bs))
    pct_lat = [(primary_by_bs[bs] - comp_by_bs[bs]) / comp_by_bs[bs] * 100 for bs in common_bs]
    pct_thr = [(bs / primary_by_bs[bs] - bs / comp_by_bs[bs]) / (bs / comp_by_bs[bs]) * 100 for bs in common_bs]
    return (common_bs, pct_lat, pct_thr)


def _batch_ax(ax, xlabel="Batch size"):
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_xlabel(xlabel)
    ax.grid(True)


def plot_model(model_name: str, batch_sizes: List[int], latencies: List[float], output_dir: Path, extra_series: Optional[List[Tuple[str, List[int], List[float]]]] = None) -> None:
    series: List[Tuple[str, List[int], List[float]]] = [(model_name, batch_sizes, latencies)]
    if extra_series:
        series.extend(extra_series)
    has_compare = len(series) > 1

    if has_compare:
        fig, ((ax_lat, ax_thr), (ax_lat_pct, ax_thr_pct)) = plt.subplots(2, 2, figsize=(10, 8))
        fig.subplots_adjust(top=0.88, hspace=0.35)
    else:
        fig, (ax_lat, ax_thr) = plt.subplots(1, 2, figsize=(10, 4))
        fig.subplots_adjust(top=0.88)
        ax_lat_pct = ax_thr_pct = None

    fig.suptitle(f"{model_name} vs {', '.join(lbl for lbl, _, _ in extra_series)}" if has_compare and extra_series else model_name, y=0.98)

    _batch_ax(ax_lat)
    ax_lat.set_ylabel("Latency (s)")
    ax_lat.set_title("Latency")
    _batch_ax(ax_thr)
    ax_thr.set_ylabel("Throughput (samples/s)")
    ax_thr.set_title("Throughput")
    for label, bs_list, lat_list in series:
        thr_list = [b / l for b, l in zip(bs_list, lat_list)]
        ax_lat.plot(bs_list, lat_list, marker="o", label=label)
        ax_thr.plot(bs_list, thr_list, marker="o", label=label)
    if has_compare:
        ax_lat.legend()
        ax_thr.legend()
        _batch_ax(ax_lat_pct)
        _batch_ax(ax_thr_pct)
        for comp_label, comp_bs, comp_lat in extra_series or []:
            bs_common, pct_lat, pct_thr = _pct_change(batch_sizes, latencies, comp_bs, comp_lat)
            if not bs_common:
                continue
            ax_lat_pct.plot(bs_common, pct_lat, marker="o", color="gray")
            ax_thr_pct.plot(bs_common, pct_thr, marker="o", color="gray")
        for ax, ylabel, title in [(ax_lat_pct, "Latency difference (%)", "Latency difference"), (ax_thr_pct, "Throughput difference (%)", "Throughput difference")]:
            ax.set_ylabel(ylabel)
            ax.set_title(title)
            ax.text(0.99, 0.99, FORMULA, transform=ax.transAxes, fontsize=9, va="top", ha="right", bbox=FORMULA_BOX)
            ax.axhline(0, color="gray", linestyle="--")

    fig.savefig(output_dir / f"{model_name.replace('.', '_')}.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    args = parse_args()
    in_path = Path(args.input)
    if not in_path.exists():
        raise SystemExit(f"Input file not found: {in_path}")
    out_dir = Path(args.output_dir) if args.output_dir else in_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    by_model = group_by_model(load_csv(args.input))
    if not by_model:
        raise SystemExit("No rows with model.bsN and metric=latency found in CSV.")

    compare_by_model: DefaultDict[str, List[Tuple[int, float]]] = defaultdict(list)
    if args.compare:
        cp = Path(args.compare)
        if not cp.exists():
            raise SystemExit(f"Compare file not found: {cp}")
        compare_by_model = group_by_model(load_csv(args.compare))

    for model_name, points in by_model.items():
        bs, lat = [p[0] for p in points], [p[1] for p in points]
        extra = matching_comparisons(model_name, compare_by_model) if args.compare else None
        plot_model(model_name, bs, lat, out_dir, extra_series=extra)
    print(f"Saved {len(by_model)} plots to {out_dir}")


if __name__ == "__main__":
    main()
