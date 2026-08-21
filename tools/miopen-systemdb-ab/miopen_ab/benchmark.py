"""Run MIOpenDriver commands in parallel and collect benchmark results."""

from __future__ import annotations

import json
import logging
import os
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from distrituner import Task, distritune

from .driver_output import parse_driver_output
from .miopendriver import command_with_driver_path, ensure_miopendriver_on_path

logger = logging.getLogger(__name__)


@dataclass
class CommandResult:
    command: str
    times_ms: list[float] = field(default_factory=list)
    median_ms: float | None = None
    stddev_ms: float | None = None
    algorithm_ids: list[str] = field(default_factory=list)
    solver_hints: list[str] = field(default_factory=list)
    returncodes: list[int] = field(default_factory=list)
    log_files: list[str] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)

    def is_complete(self, repeats: int) -> bool:
        if len(self.times_ms) < repeats:
            return False
        return all(code == 0 for code in self.returncodes[:repeats])

    def finalize(self) -> None:
        if self.times_ms:
            self.median_ms = statistics.median(self.times_ms)
            self.stddev_ms = (
                statistics.stdev(self.times_ms) if len(self.times_ms) > 1 else 0.0
            )


def load_results(path: Path) -> dict[str, CommandResult]:
    results: dict[str, CommandResult] = {}
    if not path.exists():
        return results
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            command = payload["command"]
            results[command] = CommandResult(
                command=command,
                times_ms=payload.get("times_ms", []),
                median_ms=payload.get("median_ms"),
                stddev_ms=payload.get("stddev_ms"),
                algorithm_ids=payload.get("algorithm_ids", []),
                solver_hints=payload.get("solver_hints", []),
                returncodes=payload.get("returncodes", []),
                log_files=payload.get("log_files", []),
                source_files=payload.get("source_files", []),
            )
    return results


def save_results(path: Path, results: dict[str, CommandResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for command in sorted(results):
            item = results[command]
            item.finalize()
            handle.write(json.dumps(asdict(item), sort_keys=True) + "\n")


def _merge_task_result(
    results: dict[str, CommandResult],
    command: str,
    parsed_time: float | None,
    algorithm_id: str | None,
    solver_hint: str | None,
    returncode: int,
    log_file: str | None,
    source_files: list[str] | None = None,
) -> None:
    if command not in results:
        results[command] = CommandResult(command=command, source_files=source_files or [])
    item = results[command]
    if source_files:
        for source in source_files:
            if source not in item.source_files:
                item.source_files.append(source)
    item.returncodes.append(returncode)
    if log_file:
        item.log_files.append(log_file)
    if parsed_time is not None:
        item.times_ms.append(parsed_time)
    if algorithm_id is not None:
        item.algorithm_ids.append(algorithm_id)
    if solver_hint is not None:
        item.solver_hints.append(solver_hint)


def run_benchmarks(
    commands: list[str],
    worker_envs: list[dict[str, str]],
    log_dir: Path,
    results_path: Path,
    benchmark_repeats: int = 3,
    stop_on_failure: bool = False,
    source_files_by_command: dict[str, list[str]] | None = None,
) -> dict[str, CommandResult]:
    ensure_miopendriver_on_path()
    results = load_results(results_path)
    log_dir.mkdir(parents=True, exist_ok=True)

    pending: list[tuple[str, int]] = []
    for command in commands:
        existing = results.get(command)
        completed = len(existing.times_ms) if existing else 0
        for repeat_idx in range(completed, benchmark_repeats):
            pending.append((command, repeat_idx))

    if not pending:
        logger.info("All benchmark repetitions already complete")
        save_results(results_path, results)
        return results

    tasks: list[Task] = []
    task_meta: list[tuple[str, int]] = []
    width = max(4, len(str(len(pending))))
    for idx, (command, repeat_idx) in enumerate(pending):
        run_command = command_with_driver_path(command)
        log_file = log_dir / f"{idx:0{width}d}_r{repeat_idx}.json"
        tasks.append(Task(command=run_command, log_file=log_file))
        task_meta.append((command, repeat_idx))

    logger.info("Running %d benchmark tasks (%d commands, %d repeats)", len(tasks), len(commands), benchmark_repeats)
    raw_results = distritune(tasks, worker_envs, stop_on_failure=stop_on_failure)

    for (command, _), raw, task in zip(task_meta, raw_results, tasks):
        parsed = parse_driver_output(command, raw.stdout, raw.stderr)
        log_path = str(task.log_file) if task.log_file else None
        _merge_task_result(
            results,
            command,
            parsed.time_ms,
            parsed.algorithm_id,
            parsed.solver_hint,
            raw.returncode,
            log_path,
            source_files=source_files_by_command.get(command) if source_files_by_command else None,
        )

    save_results(results_path, results)
    return results


def get_device_ids(gpus: str | None = None) -> list[str]:
    value = gpus or os.environ.get("HIP_VISIBLE_DEVICES", "")
    if not value:
        raise RuntimeError("HIP_VISIBLE_DEVICES is not set")
    return [item.strip() for item in value.split(",") if item.strip()]
