# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator

SUFFIX_RE = re.compile(r"\.(bs|dp)(\d+)")
LABELS = {"bs": "BS", "dp": "DP"}
STYLES = {"bs": dict(linestyle="-", marker="o"), "dp": dict(linestyle="--", marker="s")}
FORMULA = r"$\frac{A - B}{B} \times 100\%$"


def load_csv(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            matches = list(SUFFIX_RE.finditer(row.get("model", "")))
            if not matches:
                continue
            try:
                base = row["model"][:matches[0].start()]
                stype = matches[0].group(1)
                sval = int(matches[0].group(2))
                for m in matches[1:]:
                    base += f".{m.group(1)}{m.group(2)}"
                rows.append((base, stype, sval, float(row["performance"])))
            except (ValueError, KeyError):
                continue
    return rows


def merge_hw_bases(bases):
    """Map hardware-suffixed bases (flux.usp.hopper -> flux.usp).
    Keeps sweep-param suffixes (.bs4, .dp8) separate."""
    canonical = {}
    for base in sorted(bases, key=len):
        for shorter in sorted(bases, key=len):
            if shorter == base:
                canonical[base] = base
                break
            if base.startswith(shorter + ".") and not SUFFIX_RE.match(base[len(shorter):]):
                canonical[base] = shorter
                break
        else:
            canonical[base] = base
    return canonical


def load_all(inputs):
    """Load CSVs and group into {(base, sweep_type, hw_label): [(val, lat), ...]}."""
    raw = []
    for label, path in inputs:
        if not Path(path).exists():
            sys.exit(f"Input file not found: {path}")
        for base, stype, sval, lat in load_csv(path):
            raw.append((label, base, stype, sval, lat))

    hw_map = merge_hw_bases({base for _, base, _, _, _ in raw})
    groups = defaultdict(list)
    for label, base, stype, sval, lat in raw:
        groups[(hw_map[base], stype, label)].append((sval, lat))
    for k in groups:
        groups[k].sort()
    return groups


def throughput_numerator(base, stype, val):
    """Total images produced per run: BS for BS sweeps, DP * BS for DP sweeps."""
    m = re.search(r"\.bs(\d+)", base)
    bs = int(m.group(1)) if m else 1
    if stype == "bs":
        return val
    return val * bs


def style_ax(ax, title, xlabel, ylabel):
    ax.set(title=title, xlabel=xlabel, ylabel=ylabel)
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.grid(True)
    ax.legend(loc="best")


def main():
    p = argparse.ArgumentParser(description="Plot latency and throughput from sweep results.")
    p.add_argument("--input", action="append", nargs=2, metavar=("LABEL", "PATH"), required=True)
    p.add_argument("--output", default=None, help="Output directory")
    args = p.parse_args()

    out_dir = Path(args.output) if args.output else Path(args.input[0][1]).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    groups = load_all(args.input)
    if not groups:
        sys.exit("No sweep data found.")

    hw_order = [label for label, _ in args.input]
    colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    hw_colors = {hw: colors[i % len(colors)] for i, hw in enumerate(hw_order)}

    for base in sorted({b for b, _, _ in groups}):
        stypes = sorted({s for b, s, _ in groups if b == base})
        xlabel = LABELS[stypes[0]] if len(stypes) == 1 else "Degree"
        fixed = {m.group(1): int(m.group(2)) for m in SUFFIX_RE.finditer(base)}
        title = SUFFIX_RE.sub("", base)
        hw_present = [hw for hw in hw_order if any((base, s, hw) in groups for s in stypes)]
        has_diff = len(hw_present) == 2
        nrows = 2 if has_diff else 1

        fig, axes = plt.subplots(nrows, 2, figsize=(12, 5 * nrows), squeeze=False)
        fig.suptitle(title, y=0.98)
        fig.subplots_adjust(top=0.92 if has_diff else 0.88, hspace=0.35, wspace=0.3)

        ax_lat, ax_thr = axes[0]
        for stype in stypes:
            style = STYLES.get(stype, {})
            slabel = LABELS.get(stype, stype)
            fixed_str = ", ".join(f"{LABELS[k]}={v}" for k, v in sorted(fixed.items()))
            parts = [slabel] + ([fixed_str] if fixed_str else [])
            for hw in hw_present:
                if (base, stype, hw) not in groups:
                    continue
                vals, lats = zip(*groups[(base, stype, hw)])
                thrs = [throughput_numerator(base, stype, v) / l for v, l in zip(vals, lats)]
                label = f"{hw} ({', '.join(parts)})"
                ax_lat.plot(vals, lats, label=label, color=hw_colors[hw], **style)
                ax_thr.plot(vals, thrs, label=label, color=hw_colors[hw], **style)

        style_ax(ax_lat, "Latency", xlabel, "Latency (s)")
        style_ax(ax_thr, "Throughput", xlabel, "Images/s")

        if has_diff:
            hw_a, hw_b = hw_present
            fbox = dict(boxstyle="round,pad=0.3", facecolor="white",
                        edgecolor="gray", alpha=0.95)
            plotted = False
            for stype in stypes:
                if (base, stype, hw_a) not in groups or (base, stype, hw_b) not in groups:
                    continue
                style = STYLES.get(stype, {})
                slabel = LABELS.get(stype, stype)
                a = dict(groups[(base, stype, hw_a)])
                b = dict(groups[(base, stype, hw_b)])
                common = sorted(set(a) & set(b))
                if not common:
                    continue
                pct_lat = [(a[v] - b[v]) / b[v] * 100 for v in common]
                bs = [throughput_numerator(base, stype, v) for v in common]
                pct_thr = [(bv/a[v] - bv/b[v]) / (bv/b[v]) * 100
                           for v, bv in zip(common, bs)]
                label = slabel if len(stypes) > 1 else None
                axes[1, 0].plot(common, pct_lat, label=label, color="gray", **style)
                axes[1, 1].plot(common, pct_thr, label=label, color="gray", **style)
                plotted = True

            if plotted:
                for ax, dtitle in [(axes[1, 0], "Latency difference"),
                                   (axes[1, 1], "Throughput difference")]:
                    style_ax(ax, dtitle, xlabel, "Difference (%)")
                    ax.axhline(0, color="gray", linestyle=":")
                    ax.text(0.01, 0.99, f"A={hw_a}, B={hw_b}\n{FORMULA}",
                            transform=ax.transAxes, fontsize=9, va="top",
                            ha="left", bbox=fbox)

        out_path = out_dir / f"{title.replace('.', '_')}.png"
        fig.savefig(out_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"Saved {out_path}")


if __name__ == "__main__":
    main()
