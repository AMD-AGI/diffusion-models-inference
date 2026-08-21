#!/usr/bin/env python3
"""Orchestrate MIOpen system DB vs exhaustive tuning A/B experiment."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path


def _setup_import_paths() -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo_root / "src"))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    return repo_root


REPO_ROOT = _setup_import_paths()

from lib.artifacts import collect_artifacts, write_artifacts_manifest  # noqa: E402
from lib.benchmark import get_device_ids, run_benchmarks  # noqa: E402
from lib.compare import compare_arms, write_comparison  # noqa: E402
from lib.env_profiles import (  # noqa: E402
    ARM_A_METHODOLOGY,
    ARM_B_BENCHMARK_METHODOLOGY,
    ARM_B_TUNE_METHODOLOGY,
    arm_a_worker_envs,
    arm_b_benchmark_worker_envs,
)
from lib.metadata import collect_metadata, write_metadata  # noqa: E402
from lib.ownership import restore_host_ownership  # noqa: E402
from lib.report import write_reports  # noqa: E402
from lib.tune import merge_tuning_databases, run_exhaustive_tuning  # noqa: E402
from lib.workloads import collect_workloads, write_commands_file  # noqa: E402


logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare MIOpen system DB vs exhaustive tuning on workload convolutions"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for run artifacts",
    )
    parser.add_argument(
        "--workloads-glob",
        default="data/miopen/workloads/*.txt",
        help="Glob for MIOpenDriver command workload files",
    )
    parser.add_argument(
        "--threshold-pct",
        type=float,
        default=2.0,
        help="Relative percent threshold for improvement/regression classification",
    )
    parser.add_argument(
        "--benchmark-repeats",
        type=int,
        default=3,
        help="Number of timed repetitions per command (median reported)",
    )
    parser.add_argument(
        "--gpus",
        type=str,
        default=None,
        help="Override HIP_VISIBLE_DEVICES (comma-separated)",
    )
    parser.add_argument(
        "--skip-benchmark-a",
        action="store_true",
        help="Skip Arm A inline benchmarks",
    )
    parser.add_argument(
        "--skip-tune",
        action="store_true",
        help="Skip Arm B exhaustive tuning",
    )
    parser.add_argument(
        "--skip-benchmark-b",
        action="store_true",
        help="Skip Arm B post-tune benchmarks",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned phases and exit",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(asctime)s %(message)s",
    )
    args = parse_args()

    if args.gpus:
        os.environ["HIP_VISIBLE_DEVICES"] = args.gpus

    os.chdir(REPO_ROOT)
    workloads = collect_workloads(args.workloads_glob)
    commands = [item.command for item in workloads]
    source_files_by_command = {item.command: item.source_files for item in workloads}

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    write_commands_file(workloads, output_dir / "commands.txt")

    logger.info("Prepared %d unique commands", len(commands))
    if args.dry_run:
        logger.info("Dry run: would execute phases on output dir %s", output_dir)
        if args.gpus or os.environ.get("HIP_VISIBLE_DEVICES"):
            logger.info("GPUs: %s", args.gpus or os.environ.get("HIP_VISIBLE_DEVICES"))
        else:
            logger.info("GPUs: not set (required for actual run)")
        return 0

    device_ids = get_device_ids(args.gpus)
    experiment_config = {
        "workloads_glob": args.workloads_glob,
        "threshold_pct": args.threshold_pct,
        "benchmark_repeats": args.benchmark_repeats,
        "command_count": len(commands),
        "methodology": {
            "arm_a": ARM_A_METHODOLOGY,
            "arm_b_tune": ARM_B_TUNE_METHODOLOGY,
            "arm_b_benchmark": ARM_B_BENCHMARK_METHODOLOGY,
        },
    }

    try:
        metadata = collect_metadata(REPO_ROOT, experiment_config)
        write_metadata(output_dir / "metadata.json", metadata)

        arm_a_dir = output_dir / "arm_a"
        arm_b_dir = output_dir / "arm_b"
        arm_a_user_db = arm_a_dir / "user_db"
        arm_b_tuning = arm_b_dir / "tuning"
        arm_b_merged = arm_b_dir / "tuning_merged"

        if not args.skip_benchmark_a:
            logger.info("Phase: Arm A inline benchmarks")
            run_benchmarks(
                commands=commands,
                worker_envs=arm_a_worker_envs(device_ids, arm_a_user_db),
                log_dir=arm_a_dir / "logs",
                results_path=arm_a_dir / "results.jsonl",
                benchmark_repeats=args.benchmark_repeats,
                stop_on_failure=False,
                source_files_by_command=source_files_by_command,
            )

        if not args.skip_tune:
            logger.info("Phase: Arm B exhaustive tuning")
            run_exhaustive_tuning(
                commands=commands,
                tuning_root=arm_b_tuning,
                log_dir=arm_b_dir / "tune_logs",
                gpus=args.gpus,
                stop_on_failure=False,
            )
            merge_tuning_databases(arm_b_tuning, arm_b_merged)

        if not args.skip_benchmark_b:
            logger.info("Phase: Arm B post-tune benchmarks")
            run_benchmarks(
                commands=commands,
                worker_envs=arm_b_benchmark_worker_envs(device_ids, arm_b_merged),
                log_dir=arm_b_dir / "logs",
                results_path=arm_b_dir / "results.jsonl",
                benchmark_repeats=args.benchmark_repeats,
                stop_on_failure=False,
                source_files_by_command=source_files_by_command,
            )

        logger.info("Phase: compare and report")
        comparison = compare_arms(
            commands=commands,
            arm_a_results_path=arm_a_dir / "results.jsonl",
            arm_b_results_path=arm_b_dir / "results.jsonl",
            db_prefix=metadata.get("db_prefix", ""),
            threshold_pct=args.threshold_pct,
            benchmark_repeats=args.benchmark_repeats,
            arm_a_user_db=arm_a_user_db,
            arm_b_user_db=arm_b_merged,
            source_files_by_command=source_files_by_command,
        )
        write_comparison(output_dir / "comparison.json", comparison)
        md_path, json_path = write_reports(output_dir, metadata, comparison)

        artifacts = collect_artifacts(output_dir)
        manifest_path = write_artifacts_manifest(output_dir)
        metadata["artifacts"] = artifacts
        write_metadata(output_dir / "metadata.json", metadata)

        logger.info("Report written to %s and %s", md_path, json_path)
        logger.info("Artifact manifest written to %s", manifest_path)
        logger.info("User DB files persisted under %s", output_dir)
        return 0
    finally:
        restore_host_ownership(output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
