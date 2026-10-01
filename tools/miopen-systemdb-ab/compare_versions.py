#!/usr/bin/env python3
"""Compare two finished MIOpen system-DB A/B runs, per shape.

Reads comparison.json and metadata.json from each run directory and writes a
new report. It does not benchmark, tune, or modify either run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _setup_import_paths() -> None:
    tool_root = Path(__file__).resolve().parent
    repo_root = tool_root.parents[1]
    for path in (tool_root, repo_root / "src"):
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)


_setup_import_paths()

from miopen_ab.version_compare import compare_runs, load_run, write_version_report  # noqa: E402


def default_output_dir(baseline: Path, candidate: Path) -> Path:
    tool_root = Path(__file__).resolve().parent
    return tool_root / "comparisons" / f"{baseline.name}_vs_{candidate.name}"


def _refuse_inside_run(output_dir: Path, *run_dirs: Path) -> None:
    for run_dir in run_dirs:
        if output_dir == run_dir or run_dir in output_dir.parents:
            raise SystemExit(
                f"Output directory {output_dir} is inside run directory {run_dir}. "
                "Choose a directory outside both runs."
            )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare two finished MIOpen runs per shape. "
            "The baseline is the reference; a positive delta means the candidate is faster."
        )
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        required=True,
        help="Reference run directory (contains comparison.json)",
    )
    parser.add_argument(
        "--candidate",
        type=Path,
        required=True,
        help="Run directory of the version under evaluation",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory for report.md and report.json (must sit outside both runs)",
    )
    parser.add_argument(
        "--threshold-pct",
        type=float,
        default=2.0,
        help="Relative percent of baseline time treated as similar (default: 2.0)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    baseline_dir = args.baseline.resolve()
    candidate_dir = args.candidate.resolve()
    output_dir = (args.output_dir or default_output_dir(baseline_dir, candidate_dir)).resolve()
    _refuse_inside_run(output_dir, baseline_dir, candidate_dir)
    try:
        baseline = load_run(baseline_dir)
        candidate = load_run(candidate_dir)
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc
    report = compare_runs(baseline, candidate, args.threshold_pct)
    md_path, json_path = write_version_report(output_dir, report)
    print(md_path)
    print(json_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
