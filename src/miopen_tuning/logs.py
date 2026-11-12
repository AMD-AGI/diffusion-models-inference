# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Run MIOpen commands and save readable logs with machine-readable metadata."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .driver_output import parse_log_files, summarize_events
from .identity import utc_now


LOG_SCHEMA_VERSION = 1
LOG_PROFILES: dict[str, dict[str, str]] = {
    "off": {},
    "standard": {
        "MIOPEN_ENABLE_LOGGING_CMD": "1",
        "MIOPEN_PERFORMANCE_LOGS": "1",
        "MIOPEN_LOG_LEVEL": "5",
    },
    "diagnostic": {
        "MIOPEN_ENABLE_LOGGING": "1",
        "MIOPEN_ENABLE_LOGGING_CMD": "1",
        "MIOPEN_ENABLE_LOGGING_ELAPSED_TIME": "1",
        "MIOPEN_PERFORMANCE_LOGS": "1",
        "MIOPEN_LOG_LEVEL": "6",
    },
}
MIOPEN_ENV_PREFIXES = ("MIOPEN_", "HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES")


@dataclass(frozen=True)
class ProcessResult:
    command_id: str
    log_dir: Path
    exit_code: int
    timed_out: bool
    duration_ms: float
    metadata: dict[str, Any]
    events: list[dict[str, Any]]
    summary: dict[str, Any]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_entry(path: Path, base: Path) -> dict[str, Any]:
    return {
        "name": path.name,
        "path": str(path.relative_to(base)),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "compressed": False,
        "truncated": False,
    }


def relevant_environment(environment: dict[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in sorted(environment.items())
        if key.startswith(MIOPEN_ENV_PREFIXES) and not key.startswith("MIOPEN_CONTRACT_")
    }


def run_logged(
    command: list[str],
    *,
    command_id: str,
    log_dir: Path,
    environment: dict[str, str],
    log_profile: str,
    timeout: int,
    gpu_id: str = "",
    problem: dict[str, Any] | None = None,
    phase: str = "",
    unset_environment: tuple[str, ...] = (),
) -> ProcessResult:
    if log_profile not in LOG_PROFILES:
        raise ValueError(f"Unknown log profile: {log_profile}")
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / "stdout.log"
    stderr_path = log_dir / "stderr.log"
    command_path = log_dir / "command.txt"
    command_path.write_text(shlex.join(command) + "\n")
    process_environment = os.environ.copy()
    for name in unset_environment:
        process_environment.pop(name, None)
    process_environment.update(LOG_PROFILES[log_profile])
    process_environment.update(environment)
    started_at = utc_now()
    started = time.perf_counter()
    timed_out = False
    with stdout_path.open("w", errors="replace") as stdout, stderr_path.open("w", errors="replace") as stderr:
        try:
            completed = subprocess.run(
                command,
                stdout=stdout,
                stderr=stderr,
                text=True,
                timeout=timeout,
                env=process_environment,
                check=False,
            )
            exit_code = completed.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            exit_code = 124
            stderr.write(f"\nmiopen_tuning: command timed out after {timeout} seconds\n")
        except OSError as exc:
            exit_code = 127
            stderr.write(f"\nmiopen_tuning: failed to start command: {exc}\n")
    duration_ms = (time.perf_counter() - started) * 1000
    events = parse_log_files([stdout_path, stderr_path])
    events_path = log_dir / "events.jsonl"
    events_path.write_text("".join(json.dumps(event, sort_keys=True) + "\n" for event in events))
    summary = summarize_events(events)
    metadata: dict[str, Any] = {
        "schema_version": LOG_SCHEMA_VERSION,
        "command_id": command_id,
        "command": shlex.join(command),
        "gpu_id": gpu_id or process_environment.get("HIP_VISIBLE_DEVICES", ""),
        "started_at": started_at,
        "ended_at": utc_now(),
        "duration_ms": round(duration_ms, 3),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "phase": phase,
        "problem": problem or {},
        "environment": relevant_environment(process_environment),
        "log_profile": log_profile,
        "summary": summary,
        "files": [file_entry(path, log_dir) for path in (command_path, stdout_path, stderr_path, events_path)],
    }
    (log_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    return ProcessResult(command_id, log_dir, exit_code, timed_out, duration_ms, metadata, events, summary)


def append_index(index_path: Path, result: ProcessResult, root: Path) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "command_id": result.command_id,
        "phase": result.metadata.get("phase", ""),
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "duration_ms": round(result.duration_ms, 3),
        "log_dir": str(result.log_dir.relative_to(root)),
        "selected_solver": result.summary.get("selected_solver"),
        "event_count": result.summary.get("event_count"),
        "errors": result.summary.get("errors"),
        "warnings": result.summary.get("warnings"),
        "compiler_warnings": result.summary.get("compiler_warnings"),
    }
    with index_path.open("a") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
