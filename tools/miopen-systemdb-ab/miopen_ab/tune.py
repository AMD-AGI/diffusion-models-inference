"""Exhaustive MIOpen tuning with system DB override and DB merge."""

from __future__ import annotations

import logging
from pathlib import Path

from distrituner import Task, distritune

from .benchmark import get_device_ids
from .env_profiles import arm_b_tune_worker_envs
from .miopendriver import command_with_driver_path, ensure_miopendriver_on_path

logger = logging.getLogger(__name__)


def run_exhaustive_tuning(
    commands: list[str],
    tuning_root: Path,
    log_dir: Path,
    gpus: str | None = None,
    stop_on_failure: bool = False,
) -> None:
    ensure_miopendriver_on_path()
    device_ids = get_device_ids(gpus)
    worker_envs = arm_b_tune_worker_envs(device_ids, tuning_root)
    log_dir.mkdir(parents=True, exist_ok=True)

    width = max(4, len(str(len(commands))))
    tasks = [
        Task(
            command=command_with_driver_path(command),
            log_file=log_dir / f"{idx:0{width}d}.json",
        )
        for idx, command in enumerate(commands)
    ]

    logger.info("Starting exhaustive tuning for %d commands", len(tasks))
    distritune(tasks, worker_envs, stop_on_failure=stop_on_failure)


def merge_tuning_databases(tuning_root: Path, merged_root: Path) -> Path:
    """Merge per-device tuning outputs into a single user DB directory."""
    device_dirs = sorted(p for p in tuning_root.glob("device_*") if p.is_dir())
    if not device_dirs:
        raise FileNotFoundError(f"No tuning device directories under {tuning_root}")

    sample_udb = next(device_dirs[0].glob("*.udb.txt"), None)
    if sample_udb is None:
        raise FileNotFoundError(f"No .udb.txt files found under {device_dirs[0]}")

    stem = sample_udb.name.replace(".udb.txt", "")
    merged_root.mkdir(parents=True, exist_ok=True)
    merged_udb = merged_root / f"{stem}.udb.txt"
    merged_ufdb = merged_root / f"{stem}.ufdb.txt"

    udb_lines: set[str] = set()
    ufdb_lines: set[str] = set()
    for device_dir in device_dirs:
        for path in device_dir.glob("*.udb.txt"):
            with open(path, encoding="utf-8", errors="replace") as handle:
                udb_lines.update(line.strip() for line in handle if line.strip())
        for path in device_dir.glob("*.ufdb.txt"):
            with open(path, encoding="utf-8", errors="replace") as handle:
                ufdb_lines.update(line.strip() for line in handle if line.strip())

    with open(merged_udb, "w", encoding="utf-8") as handle:
        for line in sorted(udb_lines):
            handle.write(line + "\n")
    with open(merged_ufdb, "w", encoding="utf-8") as handle:
        for line in sorted(ufdb_lines):
            handle.write(line + "\n")

    logger.info("Merged tuning DB into %s (%d udb, %d ufdb lines)", merged_root, len(udb_lines), len(ufdb_lines))
    return merged_root
