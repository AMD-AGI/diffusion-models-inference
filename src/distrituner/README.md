# Distributed task tools

These tools distribute commands across multiple GPU worker environments. MIOpen
tuning uses `python -m miopen_tuning run`.

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
 