#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import argparse
import json
import re
import sys
import subprocess
import logging
from pathlib import Path
from typing import Any
import shutil


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


def run_arbiter_measurements(
    metrics: list[str], ref_file: Path, gen_file: Path, output_file: Path
) -> None:
    """Run arbiter measurements using the Python API and save results to JSON."""
    try:
        from arbiter import Measurement
    except ImportError as e:
        logger.error(f"Failed to import arbiter: {e}")
        return

    results: dict[str, Any] = {
        "input": str(gen_file),
        "reference": str(ref_file),
    }

    for metric in metrics:
        try:
            measurement = Measurement.get(metric)
            score = measurement().calculate((str(ref_file), str(gen_file)))
            if isinstance(score, (int, float)):
                score = float(score)
            elif isinstance(score, list):
                score = [float(s) for s in score]
            results[metric] = {"score": score, "status": "success"}
            logger.debug(f"Metric {metric}: {score}")
        except Exception as e:
            logger.warning(f"Failed to compute {metric}: {e}")
            results[metric] = {"score": None, "status": "failed", "error": str(e)}

    # Write results to JSON file
    try:
        with open(output_file, "w") as f:
            json.dump(results, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to write results to {output_file}: {e}")


def check_workload_subdirectory(
    workload_path: Path,
    reference_path_root: Path,
    image_metrics: list[str],
    video_metrics: list[str],
) -> None:
    """Check a workload subdirectory and iterate through configs."""
    logger.info(f"Checking workload path {workload_path}")

    files_png = list(workload_path.glob("*.png"))
    files_mp4 = list(workload_path.glob("*.mp4"))
    if len(files_png) + len(files_mp4) != 1:
        logger.error(
            f"Expected exactly one .png or .mp4 file in {workload_path}, found {len(files_png)} .png and {len(files_mp4)} .mp4 file(s)"
        )
        return

    if len(files_png) == 1:
        generated_image_file = files_png[0]
        reference_image_file = (
            reference_path_root / workload_path.name / "reference.png"
        )
        logger.info(
            f"Checking generated image file {generated_image_file} with reference {reference_image_file}"
        )
        if not reference_image_file.exists():
            logger.info(
                f"Reference image file {reference_image_file} does not exist, skipping."
            )
            return
        arbiter_output_file = str(Path(generated_image_file).parent / "arbiter.json")
        logger.info(f"arbiter output file: {arbiter_output_file}")
        run_arbiter_measurements(
            image_metrics,
            reference_image_file,
            generated_image_file,
            arbiter_output_file,
        )
    elif len(files_mp4) == 1:
        generated_video_file = files_mp4[0]
        reference_video_file = (
            reference_path_root / workload_path.name / "reference.mp4"
        )
        logger.info(
            f"Checking generated video file {generated_video_file} with reference {reference_video_file}"
        )
        if not reference_video_file.exists():
            logger.info(
                f"Reference video file {reference_video_file} does not exist, skipping."
            )
            return

        arbiter_output_file = str(Path(generated_video_file).parent / "arbiter.json")
        logger.info(f"arbiter output file: {arbiter_output_file}")
        run_arbiter_measurements(
            video_metrics,
            reference_video_file,
            generated_video_file,
            arbiter_output_file,
        )

    logger.info(f"Finished checking workload path {workload_path}")


def get_platform_abbreviated_name() -> str | None:
    """Infer the abbreviated platform name, such as 'mi300'."""

    if not shutil.which("amd-smi"):
        logger.error("amd-smi not found, cannot determine platform architecture")
        return None

    try:
        result = subprocess.run(
            ["amd-smi", "static", "-g", "0", "-B", "--csv"],
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            logger.error("Failed to query AMD GPU information")
            return None

        # Parse CSV output: get 5th column, 2nd row
        lines = result.stdout.strip().split("\n")
        if len(lines) < 2:
            logger.error("Could not determine platform name from amd-smi")
            return None

        platform_full_name = (
            lines[1].split(",")[4] if len(lines[1].split(",")) > 4 else ""
        )

    except Exception as e:
        logger.error(f"Failed to query AMD GPU information: {e}")
        return None

    if not platform_full_name:
        logger.error("Could not determine platform name from amd-smi")
        return None

    # Extract MI### pattern (e.g., MI300, MI355)
    match = re.search(r"MI\d{3}", platform_full_name, re.IGNORECASE)
    if not match:
        logger.error(
            f"Could not extract platform architecture from {platform_full_name}"
        )
        return None

    platform_abbreviated_name = match.group(0).lower()  # e.g., "mi300"
    return platform_abbreviated_name


def main() -> None:
    """Main function to run quality checks."""
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Run quality checks on diffusion model outputs"
    )
    parser.add_argument(
        "--reference-path",
        type=str,
        default="/app/references",
        help="Root path to reference files (default: /app/references)",
    )
    parser.add_argument(
        "--benchmark-output-path",
        type=str,
        default="/outputs",
        help="Path to benchmark output directory (default: /outputs)",
    )
    parser.add_argument(
        "--image-metrics",
        nargs="+",
        default=["lpips", "mse", "ssim"],
        help="Full-reference metrics to use for image quality checks, see arbiter documentation for available metrics",
    )
    parser.add_argument(
        "--video-metrics",
        nargs="+",
        default=["vmaf", "video_lpips", "video_mse", "video_ssim"],
        help="Full-reference metrics to use for video quality checks, see arbiter documentation for available metrics",
    )
    args = parser.parse_args()

    # Check if arbiter is available
    try:
        from arbiter import Measurement
    except ImportError:
        logger.warning("`arbiter` package not available, skipping quality checks.")
        sys.exit(0)

    platform_abbreviated_name = get_platform_abbreviated_name()
    if platform_abbreviated_name is None:
        logger.error("Failed to get platform name, skipping quality checks.")
        sys.exit(0)
    logger.info(f"Platform {platform_abbreviated_name} detected")

    reference_path_root = Path(args.reference_path) / platform_abbreviated_name
    if not reference_path_root.exists():
        logger.warning(f"Reference path {reference_path_root} does not exist")
        sys.exit(0)

    # Check benchmark output directory
    benchmark_output_dir = Path(args.benchmark_output_path)
    if not benchmark_output_dir.exists():
        logger.info(
            f"Benchmark output directory {benchmark_output_dir} does not exist, nothing to check"
        )
        sys.exit(0)

    # Iterate through workload subdirectories
    for workload_subdirectory in benchmark_output_dir.iterdir():
        if workload_subdirectory.is_dir():
            check_workload_subdirectory(
                workload_subdirectory,
                reference_path_root,
                args.image_metrics,
                args.video_metrics,
            )

    logger.info(f"Quality checks completed for platform {platform_abbreviated_name}")


if __name__ == "__main__":
    main()
