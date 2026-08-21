"""Collect and record persisted experiment artifact paths."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _glob_paths(directory: Path, pattern: str) -> list[str]:
    if not directory.is_dir():
        return []
    return sorted(str(path) for path in directory.glob(pattern))


def collect_artifacts(output_dir: Path) -> dict[str, Any]:
    """List all persisted paths under a run directory, including user DB files."""
    arm_a_user_db = output_dir / "arm_a" / "user_db"
    arm_b_tuning = output_dir / "arm_b" / "tuning"
    arm_b_merged = output_dir / "arm_b" / "tuning_merged"

    per_device: list[dict[str, Any]] = []
    for device_dir in sorted(arm_b_tuning.glob("device_*")):
        if not device_dir.is_dir():
            continue
        per_device.append(
            {
                "device_dir": str(device_dir),
                "udb_files": _glob_paths(device_dir, "*.udb.txt"),
                "ufdb_files": _glob_paths(device_dir, "*.ufdb.txt"),
            }
        )

    return {
        "output_dir": str(output_dir.resolve()),
        "host_note": (
            "When run via run_experiment.sh, output_dir is bind-mounted from the "
            "host repository and all paths below are available on the host filesystem."
        ),
        "reports": {
            "report_md": str(output_dir / "report.md"),
            "report_json": str(output_dir / "report.json"),
            "comparison_json": str(output_dir / "comparison.json"),
            "metadata_json": str(output_dir / "metadata.json"),
            "commands_txt": str(output_dir / "commands.txt"),
        },
        "arm_a": {
            "results_jsonl": str(output_dir / "arm_a" / "results.jsonl"),
            "logs_dir": str(output_dir / "arm_a" / "logs"),
            "user_db_dir": str(arm_a_user_db),
            "udb_files": _glob_paths(arm_a_user_db, "*.udb.txt"),
            "ufdb_files": _glob_paths(arm_a_user_db, "*.ufdb.txt"),
        },
        "arm_b": {
            "results_jsonl": str(output_dir / "arm_b" / "results.jsonl"),
            "benchmark_logs_dir": str(output_dir / "arm_b" / "logs"),
            "tune_logs_dir": str(output_dir / "arm_b" / "tune_logs"),
            "tuning_root": str(arm_b_tuning),
            "per_device_databases": per_device,
            "merged_db_dir": str(arm_b_merged),
            "merged_udb_files": _glob_paths(arm_b_merged, "*.udb.txt"),
            "merged_ufdb_files": _glob_paths(arm_b_merged, "*.ufdb.txt"),
        },
    }


def write_artifacts_manifest(output_dir: Path) -> Path:
    manifest_path = output_dir / "artifacts.json"
    payload = collect_artifacts(output_dir)
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return manifest_path
