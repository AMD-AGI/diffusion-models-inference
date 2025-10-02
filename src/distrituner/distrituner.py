import json
import logging
import os
import subprocess
from dataclasses import dataclass
from queue import Queue
from multiprocessing import Manager
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from tqdm import tqdm

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Task:
    """Represents a unit of work to execute in a subprocess with configuration and logging information."""

    command: str
    stdout_log_file: Path | None = None


def do_work(task: Task):
    """Execute a task in a subprocess, capturing and printing its output."""
    logging.debug(f"Worker [PID={os.getpid()}] - executing task")
    proc = subprocess.run(
        task.command,
        shell=True,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    if task.stdout_log_file is not None:
        logging.debug(
            f"Worker [PID={os.getpid()}] - saving output to {task.stdout_log_file}"
        )
        data = {
            "command": task.command,
            "stdout": proc.stdout,
            "returncode": proc.returncode,
        }
        with open(task.stdout_log_file, "w") as f:
            json.dump(data, f)


def worker_initializer(env_queue: Queue):
    """The initializer grabs the next available item from the env queue to update the worker environment.
    This guarantees each worker gets a unique environment, as Queue is thread and process safe.
    """
    env_vars = env_queue.get()
    os.environ.update(env_vars)
    logger.debug(f"Worker [PID={os.getpid()}] - initialized with {os.environ}")


def distritune(tasks: list[str], worker_envs: list[dict[str, str]]):
    """Distribute tasks across multiple GPU worker environments. Tasks are executed in parallel,
    and dynamically assigned to workers as they become available.

    Inputs:
    -------

    tasks : List[str]
        List of executables to run

    worker_envs : list[dict[str, str]]
        List of environment variable dictionaries, one per worker.
        Each environment should specify a unique GPU via HIP_VISIBLE_DEVICES to
        avoid GPU contention.
    """
    max_workers = len(worker_envs)
    with Manager() as manager:
        env_queue = manager.Queue()
        for env in worker_envs:
            env_queue.put(env)

        with ProcessPoolExecutor(
            max_workers=max_workers,
            initializer=worker_initializer,
            initargs=(env_queue,),
        ) as executor:
            logger.debug("Process pool starting work tasks")
            future_to_task = {executor.submit(do_work, task): task for task in tasks}
            for future in tqdm(
                as_completed(future_to_task), total=len(tasks), desc="DistriTune"
            ):
                task = future_to_task[future]
                try:
                    future.result()
                except Exception as exc:
                    logger.error(f"{task=} generated an exception: {exc}")
