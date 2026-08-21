"""Collect and normalize MIOpenDriver commands from workload files."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WorkloadCommand:
    command: str
    source_files: list[str] = field(default_factory=list)


_FLAG_PATTERN = re.compile(r"(?:^|\s)(-[twV])\s+\S+")


def _set_flag(command: str, flag: str, value: str) -> str:
    pattern = re.compile(rf"(?:^|\s){re.escape(flag)}\s+\S+")
    replacement = f" {flag} {value}"
    if pattern.search(command):
        return pattern.sub(replacement, command, count=1)
    return command.rstrip() + replacement


def normalize_command(command: str) -> str:
    """Ensure timing, wall-clock warmup, and verification flags are set."""
    cmd = command.strip()
    if not cmd or cmd.startswith("#"):
        return cmd
    cmd = _set_flag(cmd, "-t", "1")
    cmd = _set_flag(cmd, "-w", "2")
    cmd = _set_flag(cmd, "-V", "0")
    return cmd


def collect_workloads(workloads_glob: str) -> list[WorkloadCommand]:
    """Collect deduplicated commands and track source file provenance."""
    by_command: dict[str, WorkloadCommand] = {}

    for path in sorted(Path().glob(workloads_glob)):
        if not path.is_file():
            continue
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                raw = line.strip()
                if not raw or raw.startswith("#"):
                    continue
                command = normalize_command(raw)
                source = str(path)
                if command in by_command:
                    if source not in by_command[command].source_files:
                        by_command[command].source_files.append(source)
                else:
                    by_command[command] = WorkloadCommand(
                        command=command, source_files=[source]
                    )

    return sorted(by_command.values(), key=lambda item: item.command)


def write_commands_file(commands: list[WorkloadCommand], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        for item in commands:
            handle.write(item.command + "\n")
