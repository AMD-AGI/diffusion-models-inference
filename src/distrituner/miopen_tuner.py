# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import argparse
import logging
import os
import sys
from pathlib import Path

from distrituner import Task, distritune

logger = logging.getLogger(__name__)


def create_miopen_worker_environment(
    device_id: str | int,
    tuning_database_path: Path | str,
    miopen_find_mode: int = 1,
    miopen_find_enforce: int = 4,
    miopen_debug_conv_direct: int | None = None,
) -> dict[str, str]:
    """Create environment variables for a worker for MIOpen tuning."""
    env = {
        "HIP_VISIBLE_DEVICES": f"{device_id}",
        "MIOPEN_FIND_MODE": f"{miopen_find_mode}",
        "MIOPEN_FIND_ENFORCE": f"{miopen_find_enforce}",
        "MIOPEN_USER_DB_PATH": str(tuning_database_path),
    }
    if miopen_debug_conv_direct is not None:
        env["MIOPEN_DEBUG_CONV_DIRECT"] = str(miopen_debug_conv_direct)
    return env


def main():
    logging.basicConfig(
        level=logging.INFO, format="[%(levelname)s] %(asctime)s %(message)s"
    )

    argparser = argparse.ArgumentParser(
        description="Distribute MIOpen tuning work across multiple GPUs in parallel"
    )
    argparser.add_argument(
        "task_file", type=str, help="File with MIOpen driver commands to use for tuning"
    )
    argparser.add_argument(
        "--tuning-output-path",
        type=str,
        default="./tuning",
        help="Path where to output MIOpen User DB files, subdirectories will be created per GPU",
    )
    argparser.add_argument(
        "--miopen-find-mode",
        type=int,
        default=1,
        help="Value to set for MIOPEN_FIND_MODE",
    )
    argparser.add_argument(
        "--miopen-find-enforce",
        type=int,
        default=4,
        help="Value to set for MIOPEN_FIND_ENFORCE",
    )
    argparser.add_argument(
        "--miopen-debug-conv-direct",
        type=int,
        help="Value to set for MIOPEN_DEBUG_CONV_DIRECT - if set to 0, will disable naive solver to speed up tuning",
    )
    argparser.add_argument(
        "--log-dir",
        type=str,
        default="./logs",
        help="Path where to log information about the individual tuning tasks",
    )
    argparser.add_argument(
        "--stop-on-failure",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Stop and cancel remaining tasks on first failure (default: True). Use --no-stop-on-failure to continue running all tasks despite failures.",
    )
    args = argparser.parse_args()

    log_dir = Path(args.log_dir).expanduser().resolve()
    if log_dir.is_file():
        logging.error(
            f"Error: --log-dir {args.log_dir} is a file. Please provide a directory path."
        )
        sys.exit(1)
    log_dir.mkdir(parents=True, exist_ok=True)

    with open(args.task_file, "r") as f:
        lines = f.readlines()

    n_leading_zeros_format = f"0{len(str(len(lines)))}"
    tasks = [
        Task(
            command=line.strip(),
            log_file=log_dir / f"{ii:{n_leading_zeros_format}}.json",
        )
        for ii, line in enumerate(lines)
    ]
    logging.info(f"Read {len(tasks)} MIOpen tuning tasks from {args.task_file}")

    tuning_database_path = Path(args.tuning_output_path).expanduser().resolve()
    if tuning_database_path.is_file():
        logging.error(
            f"Error: --tuning-output-path {args.tuning_output_path} is a file. Please provide a directory path."
        )
        sys.exit(1)
    tuning_database_path.mkdir(parents=True, exist_ok=True)
    logging.info(f"Using path {str(tuning_database_path)} for results")

    if "HIP_VISIBLE_DEVICES" not in os.environ:
        # TODO: infer number of GPUs available on system and use all by default
        logging.error(
            "Please set HIP_VISIBLE_DEVICES to indicate which devices should be used for tuning. Exiting."
        )
        sys.exit(1)

    # Create environments for each of the workers
    device_ids = os.environ.get("HIP_VISIBLE_DEVICES").split(",")
    worker_envs = []
    for ii, device_id in enumerate(device_ids):
        env = create_miopen_worker_environment(
            device_id.strip(),
            tuning_database_path=tuning_database_path / f"device_{device_id}",
            miopen_find_mode=args.miopen_find_mode,
            miopen_find_enforce=args.miopen_find_enforce,
            miopen_debug_conv_direct=args.miopen_debug_conv_direct,
        )
        worker_envs.append(env)
        logging.debug(f"Worker {ii} will use environment: {env}")

    try:
        results = distritune(tasks, worker_envs=worker_envs, stop_on_failure=args.stop_on_failure)
    except Exception as e:
        logging.error(f"Tuning failed: {e}")
        sys.exit(1)

    failed_count = sum(1 for r in results if r.returncode != 0)
    logging.info(f"Completed {len(results)} MIOpen tuning tasks successfully")
    if failed_count > 0:
        sys.exit(1)

    if len(results) > 0:
        logging.info(f"Sum of task durations: {sum(result.duration_ms for result in results):.2f} ms")
        logging.info(f"Average duration: {sum(result.duration_ms for result in results) / len(results):.2f} ms")
        logging.info(f"Minimum duration: {min(result.duration_ms for result in results):.2f} ms")
        logging.info(f"Maximum duration: {max(result.duration_ms for result in results):.2f} ms")


if __name__ == "__main__":
    main()
