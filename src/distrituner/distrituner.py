import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, asdict
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
    log_file: Path | None = None


@dataclass(frozen=True)
class Result:
    """Represents the result of a task execution."""

    command: str
    returncode: int
    stdout: str
    stderr: str
    duration_ms: float

def do_work(task: Task) -> Result:
    """Execute a task in a subprocess, capturing and printing its output.
    
    Returns:
        A Result object containing the task command, exit code, stdout, stderr, and duration.
    """
    logging.debug(f"Worker [PID={os.getpid()}] - executing task")
    start_timestamp = time.perf_counter_ns()
    proc = subprocess.run(
        task.command,
        shell=True,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    end_timestamp = time.perf_counter_ns()
    duration_ms = (end_timestamp - start_timestamp) / 1000000
    result = Result(
        command=task.command,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        duration_ms=duration_ms,
    )

    if task.log_file is not None:
        logging.debug(
            f"Worker [PID={os.getpid()}] - saving output to {task.log_file}"
        )
        with open(task.log_file, "w") as f:
            json.dump(asdict(result), f)
    
    return result


def worker_initializer(env_queue: Queue):
    """The initializer grabs the next available item from the env queue to update the worker environment.
    This guarantees each worker gets a unique environment, as Queue is thread and process safe.
    """
    env_vars = env_queue.get()
    os.environ.update(env_vars)
    logger.debug(f"Worker [PID={os.getpid()}] - initialized with {os.environ}")


def distritune(
    tasks: list[Task],
    worker_envs: list[dict[str, str]],
    stop_on_failure: bool = True,
) -> list[Result]:
    """Distribute tasks across multiple GPU worker environments. Tasks are executed in parallel,
    and dynamically assigned to workers as they become available.

    Inputs:
    -------

    tasks : list[Task]
        List of Task objects to execute

    worker_envs : list[dict[str, str]]
        List of environment variable dictionaries, one per worker.
        Each environment should specify a unique GPU via HIP_VISIBLE_DEVICES to
        avoid GPU contention.

    stop_on_failure : bool, optional
        If True (default), cancel all remaining tasks and raise an exception when
        any task fails with a non-zero exit code. If False, continue running all
        tasks regardless of failures and only log errors.

    Returns:
    --------
    list[Result]
        List of Result objects, one for each task.
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
            results = []
            for future in tqdm(
                as_completed(future_to_task), total=len(tasks), desc="DistriTune"
            ):
                task = future_to_task[future]
                try:
                    result = future.result()
                    results.append(result)
                    if result.returncode != 0:
                        logger.error(f"{task=} exited with non-zero code: {result.returncode}")
                        if stop_on_failure:
                            for f in future_to_task:
                                if not f.done():
                                    f.cancel()
                            raise RuntimeError(f"Task {task} failed with exit code {result.returncode}")
                except Exception as exc:
                    logger.error(f"{task=} generated an exception: {exc}")
                    for f in future_to_task:
                        if not f.done():
                            f.cancel()
                    raise

    failed_count = sum(1 for r in results if r.returncode != 0)
    if not stop_on_failure and failed_count > 0:
        logger.error(f"Completed with {failed_count} failed tasks (non-zero return codes)")
        for r in results:
            if r.returncode != 0:
                logger.error(f"  - {r.command}")
        raise RuntimeError(f"{failed_count} tasks failed. See logs for details.")

    return results
