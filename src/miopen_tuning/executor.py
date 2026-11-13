# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Execute MIOpen tuning commands across GPUs."""

from __future__ import annotations

import queue
import shlex
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .logs import ProcessResult, append_index, run_logged


@dataclass(frozen=True)
class TuningTask:
    command_id: str
    command: str
    problem: dict[str, Any]


@dataclass(frozen=True)
class TuningResult:
    task: TuningTask
    device_id: str
    db_directory: Path
    process: ProcessResult

    @property
    def succeeded(self) -> bool:
        return self.process.exit_code == 0 and not self.process.timed_out


def device_ids(value: str) -> list[str]:
    devices = [device.strip() for device in value.split(",") if device.strip()]
    if not devices:
        raise ValueError("At least one GPU device is required")
    return devices


def execute_tasks(
    tasks: list[TuningTask],
    *,
    devices: list[str],
    driver: str,
    run_directory: Path,
    environment: dict[str, str],
    log_profile: str,
    timeout: int,
) -> list[TuningResult]:
    task_queue: queue.Queue[tuple[int, TuningTask]] = queue.Queue()
    for index, task in enumerate(tasks):
        task_queue.put((index, task))
    results: list[TuningResult | None] = [None] * len(tasks)
    lock = threading.Lock()
    logs_directory = run_directory / "tune" / "logs"
    logs_directory.mkdir(parents=True, exist_ok=True)

    def worker(device_id: str) -> None:
        db_directory = run_directory / "db" / "tuned" / f"device-{device_id}"
        db_directory.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                index, task = task_queue.get_nowait()
            except queue.Empty:
                return
            command = shlex.split(task.command)
            command[0] = driver
            process = run_logged(
                command,
                command_id=task.command_id,
                log_dir=logs_directory / task.command_id,
                environment=environment
                | {
                    "HIP_VISIBLE_DEVICES": device_id,
                    "MIOPEN_USER_DB_PATH": str(db_directory),
                },
                log_profile=log_profile,
                timeout=timeout,
                gpu_id=device_id,
                problem=task.problem,
                phase="tune",
            )
            result = TuningResult(task, device_id, db_directory, process)
            with lock:
                results[index] = result
                append_index(logs_directory / "index.jsonl", process, run_directory)
            task_queue.task_done()

    threads = [threading.Thread(target=worker, args=(device,), name=f"miopen-gpu-{device}") for device in devices]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if any(result is None for result in results):
        raise RuntimeError("Not every tuning task reached a terminal result")
    return [result for result in results if result is not None]
