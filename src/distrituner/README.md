# Distributed tuning tools (MIOpen, hipblaslt-bench)

These tools help distribute work across multiple GPUs in parallel.


## MIOpen tuning
Set `HIP_VISIBLE_DEVICES` to control which devices are used for tuning. Additionally, you need a file containing MIOpenDriver commands for the tuning you want to execute.

Example:
```
HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 python miopen_tuner.py drivercmds.txt
```

The MIOpen database files are written to `./tuning/device{i}/`, where `{i}` indicates device index. The target directory can be controlled by setting `--tuning-output-path [dir]`.

The tuner writes log files for each tuning task to folder `./logs`, this can be controlled by the argument `--log-dir [dir]`.

The arguments `--miopen-find-mode [int]` and `--miopen-find-enforce [int]` control each GPU worker's `MIOPEN_FIND_MODE` and `MIOPEN_FIND_ENFORCE` environment variables, respectively.

By default, the tuner stops on the first failure. Use `--no-stop-on-failure` to continue running all tasks even if some fail.


## DistriTune API

The core `distritune` function provides task distribution with configurable failure handling:

### Parameters

- `tasks`: List of `Task` objects (command + optional log file)
- `worker_envs`: List of environment variable dictionaries (one per GPU worker)
- `stop_on_failure`: Boolean flag (default: `True`)
  - `True`: Cancel remaining tasks and exit immediately on first failure
  - `False`: Continue running all tasks, then report failures at the end

### Task Configuration

Each `Task` object can specify:
- `command`: Shell command to execute (required)
- `log_file`: Path to save command output as JSON, including both stdout and stderr (optional)

The log file contains a JSON object with:
- `command`: The command that was executed
- `stdout`: Standard output from the command
- `stderr`: Standard error from the command
- `returncode`: Exit code of the command

`distritune()` returns one `Result` per task **in the same order as the input
`tasks` list**, even though tasks may finish in a different order when run in
parallel.

### Example Usage
```python
from distrituner import Task, distritune

# Define tasks
tasks = [
    Task(command="./benchmark --config1"),
    Task(command="./benchmark --config2"),
    Task(command="./benchmark --config3"),
]

# Define worker environments (one per GPU)
worker_envs = [
    {"HIP_VISIBLE_DEVICES": "0"},
    {"HIP_VISIBLE_DEVICES": "1"},
]

# Stop on first failure (default behavior)
distritune(tasks, worker_envs, stop_on_failure=True)

# Or continue running all tasks despite failures
distritune(tasks, worker_envs, stop_on_failure=False)

# Capture stdout and stderr to log files
tasks_with_logging = [
    Task(command="./benchmark --config1", log_file="logs/task1.json"),
    Task(command="./benchmark --config2", log_file="logs/task2.json"),
]
distritune(tasks_with_logging, worker_envs)
```

### Running Tests

```bash
source .venv/bin/activate
PYTHONPATH=src pytest src/distrituner/tests/test_distrituner.py -v
```
 