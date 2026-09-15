# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import json
import logging
from pathlib import Path
from typing import Iterable


logger = logging.getLogger(__name__)

_TRUTHY_VALUES = {"1", "true", "yes", "on"}
_DRIVER_MARKER = "MIOpenDriver "


def env_enabled(value: str) -> bool:
    """Return whether an environment-variable value enables a feature."""
    return value.strip().lower() in _TRUTHY_VALUES


class MIOpenDriverCommandCollector:
    """Collect per-workload MIOpenDriver commands and maintain a manifest."""

    def __init__(self, results_directory: Path, workload_names: Iterable[str]):
        self.output_directory = results_directory / "miopen_workloads"
        self.output_directory.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.output_directory / "manifest.json"
        self._workloads = {
            name: {
                "name": name,
                "benchmark_status": "not_run",
                "collection_status": "not_started",
                "command_count": 0,
                "file": None,
            }
            for name in workload_names
        }
        self._write_manifest()

    def collect(self, workload_name: str, stderr_path: Path, process_succeeded: bool) -> None:
        """Extract commands emitted by one completed benchmark process."""
        entry = self._workloads[workload_name]
        entry["benchmark_status"] = (
            "process_succeeded" if process_succeeded else "process_failed"
        )

        successful_path = self.output_directory / f"{workload_name}.txt"
        partial_path = self.output_directory / f"{workload_name}.partial.txt"
        for path in (successful_path, partial_path):
            path.unlink(missing_ok=True)

        try:
            commands = self._extract_commands(stderr_path)
            entry["command_count"] = len(commands)
            if commands:
                output_path = successful_path if process_succeeded else partial_path
                output_path.write_text("".join(f"{command}\n" for command in commands))
                entry["file"] = output_path.name
                entry["collection_status"] = "collected"
            else:
                entry["file"] = None
                entry["collection_status"] = "no_commands"
        except OSError as exc:
            entry["file"] = None
            entry["collection_status"] = "error"
            logger.warning(
                "Failed to collect MIOpenDriver commands for %s: %s",
                workload_name,
                exc,
            )
        self._write_manifest_safely()

    def mark_succeeded(self, workload_name: str) -> None:
        self._set_benchmark_status(workload_name, "succeeded")

    def mark_metrics_failed(self, workload_name: str) -> None:
        self._set_benchmark_status(workload_name, "metrics_failed")

    @staticmethod
    def _extract_commands(stderr_path: Path) -> list[str]:
        commands = set()
        with stderr_path.open(errors="replace") as stderr_file:
            for line in stderr_file:
                marker_position = line.find(_DRIVER_MARKER)
                if marker_position >= 0:
                    commands.add(line[marker_position:].strip())
        return sorted(commands)

    def _set_benchmark_status(self, workload_name: str, status: str) -> None:
        self._workloads[workload_name]["benchmark_status"] = status
        self._write_manifest_safely()

    def _write_manifest_safely(self) -> None:
        try:
            self._write_manifest()
        except OSError as exc:
            logger.warning("Failed to write MIOpen workload manifest: %s", exc)

    def _write_manifest(self) -> None:
        manifest = {"workloads": list(self._workloads.values())}
        temporary_path = self.manifest_path.with_suffix(".json.tmp")
        temporary_path.write_text(json.dumps(manifest, indent=2) + "\n")
        temporary_path.replace(self.manifest_path)
