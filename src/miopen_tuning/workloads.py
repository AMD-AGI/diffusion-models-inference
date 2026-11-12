# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Load and validate MIOpenDriver workload files."""

from __future__ import annotations

import hashlib
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .problem import MIOpenConvolution


REQUIRED_FLAGS = {
    "batchsize": ("-n", "--batchsize"),
    "in_channels": ("-c", "--in_channels"),
    "in_h": ("-H", "--in_h"),
    "in_w": ("-W", "--in_w"),
    "out_channels": ("-k", "--out_channels"),
    "fil_h": ("-y", "--fil_h"),
    "fil_w": ("-x", "--fil_w"),
}
REQUIRED_3D_FLAGS = {
    "fil_d": ("-@", "--fil_d"),
}


@dataclass(frozen=True)
class Source:
    path: str
    line: int

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "line": self.line}


@dataclass
class WorkloadProblem:
    command_id: str
    command: str
    problem: MIOpenConvolution
    find_db_key: str
    perf_db_key: str
    sources: list[Source] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "command": self.command,
            "find_db_key": self.find_db_key,
            "perf_db_key": self.perf_db_key,
            "sources": [source.to_dict() for source in self.sources],
        }


@dataclass(frozen=True)
class RejectedCommand:
    source: Source
    command: str
    error: str

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source.to_dict(), "command": self.command, "error": self.error}


@dataclass
class WorkloadSet:
    files: list[str]
    problems: dict[str, WorkloadProblem]
    rejected: list[RejectedCommand]
    command_count: int
    duplicate_count: int
    format_warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "files": self.files,
            "command_count": self.command_count,
            "unique_problem_count": len(self.problems),
            "duplicate_count": self.duplicate_count,
            "rejected_count": len(self.rejected),
            "format_warnings": self.format_warnings,
            "problems": [problem.to_dict() for problem in self.problems.values()],
            "rejected": [rejected.to_dict() for rejected in self.rejected],
        }


def workload_files(paths: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.glob("*.txt")))
        else:
            files.append(path)
    return files


def _has_flag(tokens: list[str], flags: tuple[str, ...]) -> bool:
    return any(flag in tokens for flag in flags)


def validate_command(command: str) -> MIOpenConvolution:
    tokens = shlex.split(command)
    if len(tokens) < 2 or tokens[0] != "MIOpenDriver" or not tokens[1].startswith("conv"):
        raise ValueError("Expected an MIOpenDriver convolution command")
    missing = [name for name, flags in REQUIRED_FLAGS.items() if not _has_flag(tokens, flags)]
    if _has_flag(tokens, ("-!", "--in_d")):
        missing.extend(name for name, flags in REQUIRED_3D_FLAGS.items() if not _has_flag(tokens, flags))
    if missing:
        raise ValueError(f"Missing required flags: {', '.join(missing)}")
    problem = MIOpenConvolution.from_miopendriver_command(command)
    problem.to_find_db_key()
    return problem


def command_id(find_db_key: str) -> str:
    return hashlib.sha256(find_db_key.encode()).hexdigest()[:16]


def load_workloads(paths: Iterable[Path], root: Path | None = None) -> WorkloadSet:
    files = workload_files(paths)
    problems: dict[str, WorkloadProblem] = {}
    rejected: list[RejectedCommand] = []
    command_count = 0
    duplicate_count = 0
    format_warnings: list[str] = []
    for path in files:
        text = path.read_text()
        commands = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
        if text and not text.endswith("\n"):
            format_warnings.append(f"{path}: missing trailing newline")
        if commands != sorted(commands):
            format_warnings.append(f"{path}: commands are not sorted")
        if len(commands) != len(set(commands)):
            format_warnings.append(f"{path}: file contains duplicate commands")
        display_path = str(path.relative_to(root)) if root and path.is_relative_to(root) else str(path)
        for line_number, raw in enumerate(text.splitlines(), 1):
            command = raw.strip()
            if not command or command.startswith("#"):
                continue
            command_count += 1
            source = Source(display_path, line_number)
            try:
                problem = validate_command(command)
                find_key = problem.to_find_db_key()
                perf_key = problem.to_perf_db_key()
            except (ValueError, IndexError) as exc:
                rejected.append(RejectedCommand(source, command, str(exc)))
                continue
            if find_key in problems:
                problems[find_key].sources.append(source)
                duplicate_count += 1
            else:
                problems[find_key] = WorkloadProblem(command_id(find_key), command, problem, find_key, perf_key, [source])
    return WorkloadSet([str(path) for path in files], problems, rejected, command_count, duplicate_count, format_warnings)
