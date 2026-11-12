# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Load and validate MIOpenDriver workload files."""

from __future__ import annotations

import hashlib
import json
import re
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


_ARCH_RE = re.compile(r"gfx[0-9a-z]+")


def infer_architecture(path: Path) -> str:
    match = _ARCH_RE.search(path.name)
    if not match:
        raise ValueError(f"Cannot infer architecture from {path}; use --arch")
    return match.group(0)


def _commands(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.strip().startswith("#")]


def target_workload_path(workloads_dir: Path, name: str, architecture: str) -> Path:
    architecture_path = workloads_dir / f"{name}.{architecture}.txt"
    if architecture_path.exists():
        return architecture_path
    return workloads_dir / f"{name}.txt"


def import_collected_workloads(
    artifact_dirs: Iterable[Path],
    workloads_dir: Path,
    *,
    architecture: str | None = None,
    include_partial: bool = False,
    write: bool = False,
) -> dict[str, Any]:
    imports: list[dict[str, Any]] = []
    writes: dict[Path, set[str]] = {}
    for artifact_dir in artifact_dirs:
        arch = architecture or infer_architecture(artifact_dir)
        manifest_path = artifact_dir / "manifest.json"
        if not manifest_path.is_file():
            raise ValueError(f"Missing workload collection manifest: {manifest_path}")
        manifest = json.loads(manifest_path.read_text())
        for workload in manifest.get("workloads", []):
            name = workload.get("name")
            file_name = workload.get("file")
            entry: dict[str, Any] = {
                "artifact": str(artifact_dir),
                "architecture": arch,
                "workload": name,
                "benchmark_status": workload.get("benchmark_status"),
                "collection_status": workload.get("collection_status"),
                "source": file_name,
                "target": None,
                "status": "skipped",
                "added": [],
                "removed": [],
                "unchanged": 0,
                "rejected": [],
                "reasons": [],
            }
            imports.append(entry)
            if not name or not file_name:
                entry["reasons"].append("no collected command file")
                continue
            partial = file_name.endswith(".partial.txt") or workload.get("benchmark_status") not in {"succeeded", "process_succeeded"}
            if partial and not include_partial:
                entry["status"] = "flagged"
                entry["reasons"].append("collected from a failed or incomplete benchmark")
                continue
            source = artifact_dir / file_name
            target = target_workload_path(workloads_dir, name, arch)
            entry["target"] = str(target)
            valid: set[str] = set()
            for line_number, command in enumerate(_commands(source), 1):
                try:
                    validate_command(command)
                except (ValueError, IndexError) as exc:
                    entry["rejected"].append({"line": line_number, "command": command, "error": str(exc)})
                else:
                    valid.add(command)
            if entry["rejected"]:
                entry["status"] = "rejected"
                entry["reasons"].append("collected file contains invalid commands")
                continue
            existing = set(_commands(target))
            entry["added"] = sorted(valid - existing)
            entry["removed"] = sorted(existing - valid)
            entry["unchanged"] = len(valid & existing)
            entry["status"] = "changed" if entry["added"] or entry["removed"] else "unchanged"
            if target in writes and writes[target] != valid:
                raise ValueError(f"Conflicting collected commands for {target}")
            writes[target] = valid
    if write:
        for target, commands in writes.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("".join(f"{command}\n" for command in sorted(commands)))
    counts = {
        status: sum(entry["status"] == status for entry in imports)
        for status in ("changed", "unchanged", "flagged", "rejected", "skipped")
    }
    return {"written": write, "counts": counts, "imports": imports}


def import_report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "## MIOpen workload import",
        "",
        "| Architecture | Workload | Status | Added | Removed | Unchanged | Target |",
        "| --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for entry in report["imports"]:
        target = entry["target"] or ""
        lines.append(
            f"| {entry['architecture']} | {entry['workload']} | {entry['status']} | "
            f"{len(entry['added'])} | {len(entry['removed'])} | {entry['unchanged']} | `{target}` |"
        )
    return "\n".join(lines) + "\n"
