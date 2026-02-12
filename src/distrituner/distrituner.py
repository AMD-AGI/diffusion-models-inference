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
    log_file: Path | None = None


def do_work(task: Task) -> int:
    """Execute a task in a subprocess, capturing and printing its output.
    
    Returns:
        The exit code of the subprocess.
    """
    logging.debug(f"Worker [PID={os.getpid()}] - executing task")
    proc = subprocess.run(
        task.command,
        shell=True,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if task.log_file is not None:
        logging.debug(
            f"Worker [PID={os.getpid()}] - saving output to {task.log_file}"
        )
        data = {
            "command": task.command,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "returncode": proc.returncode,
        }
        with open(task.log_file, "w") as f:
            json.dump(data, f)
    
    return proc.returncode


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
) -> None:
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
    """
    max_workers = len(worker_envs)
    failed_tasks = []
    
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
                    returncode = future.result()
                    if returncode != 0:
                        logger.error(f"{task=} exited with non-zero code: {returncode}")
                        failed_tasks.append((task, returncode))
                        
                        if stop_on_failure:
                            # Cancel all remaining tasks
                            for f in future_to_task:
                                if not f.done():
                                    f.cancel()
                            raise RuntimeError(f"Task {task} failed with exit code {returncode}")
                except Exception as exc:
                    logger.error(f"{task=} generated an exception: {exc}")
                    failed_tasks.append((task, exc))
                    
                    if stop_on_failure:
                        # Cancel all remaining tasks
                        for f in future_to_task:
                            if not f.done():
                                f.cancel()
                        raise
    
    # If we didn't stop on failure but have failed tasks, report them
    if not stop_on_failure and failed_tasks:
        logger.error(f"Completed with {len(failed_tasks)} failed tasks:")
        for task, error in failed_tasks:
            if isinstance(error, int):
                logger.error(f"  - {task} failed with exit code {error}")
            else:
                logger.error(f"  - {task} failed with exception: {error}")
        raise RuntimeError(f"{len(failed_tasks)} tasks failed. See logs for details.")
