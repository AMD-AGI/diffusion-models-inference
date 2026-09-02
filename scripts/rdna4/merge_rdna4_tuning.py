#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Merge per-rank RDNA4 tuning CSVs into artifacts for the final image."""

import argparse
import csv
from pathlib import Path


def csv_files(root: Path, output: Path):
    return [p for p in sorted(root.rglob("*.csv")) if p.resolve() != output.resolve()]


def merge_tunable(root: Path, output: Path) -> bool:
    validators = []
    seen_validators = set()
    best = {}
    for path in csv_files(root, output):
        for raw in path.read_text(errors="replace").splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("Validator,"):
                if line not in seen_validators:
                    validators.append(line)
                    seen_validators.add(line)
                continue
            fields = next(csv.reader([line]))
            if len(fields) < 3:
                continue
            key = (fields[0], fields[1])
            try:
                elapsed = float(fields[3]) if len(fields) >= 4 else float("inf")
            except ValueError:
                elapsed = float("inf")
            if key not in best or elapsed < best[key][0]:
                best[key] = (elapsed, line)
    if not best:
        return False
    output.write_text("\n".join(validators + [item[1] for item in best.values()]) + "\n")
    return True


def merge_aiter(root: Path, output: Path) -> bool:
    header = None
    best = {}
    for path in csv_files(root, output):
        indexes = None
        for raw in path.read_text(errors="replace").splitlines():
            line = raw.strip()
            if not line:
                continue
            fields = next(csv.reader([line]))
            if {"M", "N", "K"}.issubset(fields):
                header = header or line
                indexes = {name: i for i, name in enumerate(fields)}
                continue
            if not indexes or max(indexes[k] for k in ("M", "N", "K")) >= len(fields):
                continue
            key = tuple(fields[indexes[k]] for k in ("M", "N", "K"))
            try:
                elapsed = float(fields[indexes["us"]]) if "us" in indexes else float("inf")
            except (ValueError, IndexError):
                elapsed = float("inf")
            if key not in best or elapsed < best[key][0]:
                best[key] = (elapsed, line)
    if not header or not best:
        return False
    output.write_text("\n".join([header] + [item[1] for item in best.values()]) + "\n")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tunable-dir", type=Path, required=True)
    parser.add_argument("--aiter-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    tunable = args.output_dir / "tunableop_results_merged.csv"
    aiter = args.output_dir / "a8w8_blockscale_tuned_gemm_merged.csv"
    print(f"TunableOp artifact: {tunable if merge_tunable(args.tunable_dir, tunable) else 'not produced'}")
    print(f"Aiter artifact: {aiter if merge_aiter(args.aiter_dir, aiter) else 'not produced'}")


if __name__ == "__main__":
    main()
