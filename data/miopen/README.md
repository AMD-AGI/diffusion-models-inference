# MIOpen user database

Public `amdsiloai/pytorch-xdit` images need buildin MIOpen user databases for reaching
maximal benchmark performance on selected example workloads. Here you find instructions
for generating these user databases as well as pre-created databases to be used with
the public images.

## Use

Point

```
MIOPEN_USER_DB_PATH
```

environment variable to the directory cotaining the user database.

## Generate

From the repository root, run

```
docker build -f docker/Dockerfile[.ci] -t pytorch_xdit_core --target core .
```

to build the base image with the default build arguments. For the repository root, run

````
docker run \
    --cap-add=SYS_PTRACE \
    --security-opt seccomp=unconfined --privileged \
    --device=/dev/kfd --device=/dev/dri \
    --group-add video \
    --ipc=host --network host \
    --user root \
    --shm-size 128G \
    --rm \
    --mount type=bind,src=.,dst=/app/diffusion-models-inference \
    -e HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 \
    -e OMP_NUM_THREADS=16 \
    -e MIOPEN_USER_DB_PATH=/app/diffusion-models-inference/data/miopen/userdb \
    -e MIOPEN_FIND_MODE=1 \
    -e MIOPEN_FIND_ENFORCE=3 \
    -e HOST_UID=$(id -u) \
    pytorch_xdit_core \
    bash /app/diffusion-models-inference/data/miopen/tune.sh
```

to generate the user database.

## Collect workload commands

The Build and Benchmark workflow enables command-only MIOpen logging for every
benchmark run. It uploads one artifact per architecture named
`<run-number>-miopen-workloads-<architecture>`. Full MIOpen API logging is not
enabled.

Each artifact contains:

- `<workload>.txt` for a benchmark process that exited successfully.
- `<workload>.partial.txt` for commands captured before a benchmark process
  exited unsuccessfully. The import command flags these files by default.
- `manifest.json`, which records every selected workload, its benchmark and
  collection statuses, its unique command count, and its output filename.
  Workloads that emitted no commands have no text file but remain in the
  manifest.

Commands are de-duplicated within each workload and sorted. They are not
de-duplicated across workloads or architecture artifacts because the tuning
planner handles that while keeping each workload source.

Compare downloaded artifacts with the repository workload files:

```sh
PYTHONPATH=src python -m miopen_tuning workloads import \
  /tmp/123-miopen-workloads-gfx950 \
  --output /tmp/miopen-workload-import.json
```

The report lists added, removed, and unchanged commands for each workload and
architecture. It flags `.partial.txt` files and rejects invalid commands. Add
`--write` to update the matching workload files. When a file named
`<workload>.<architecture>.txt` already exists, the import updates that file;
otherwise it updates `<workload>.txt`. Use `--include-partial` only after
reviewing commands from failed benchmark runs.

Collection is disabled by default when `.ci/run.py` is invoked directly. To
enable it for an interactive run, set
`CI_RUN_PY_COLLECT_MIOPEN_DRIVER_COMMANDS=1`. The values `true`, `yes`, and
`on` are also accepted, case-insensitively.

## Test the tuning tools

The MIOpen tuning tools have CPU-only tests that run without a GPU, ROCm image,
or MIOpen installation:

```sh
python -m pip install pytest tqdm
PYTHONPATH=src:.ci python -m pytest -q \
  src/distrituner/tests \
  src/miopen_tuning/tests \
  .ci/tests/test_miopen_driver_commands.py
```

Tests marked as expected failures document known defects. When a fix makes one
of these tests pass, remove its expected-failure marker in the same change.

## Check MIOpen behavior

GPU contract tests check MIOpen behavior that the tuning tools depend on. Run
them inside the ROCm image being tested:

```sh
MIOPEN_CONTRACT_TESTS=1 \
MIOPEN_CONTRACT_ARTIFACTS=/tmp/miopen-contracts \
PYTHONPATH=src \
python -m unittest -v miopen_tuning.tests.miopen.test_miopen_contracts
```

The artifact directory contains readable `stdout.log` and `stderr.log` files,
parsed `events.jsonl`, metadata, tuned DBs, and image identity. It must be a new
or empty directory; the tests refuse to overwrite existing files. The manual
`MIOpen contracts` GitHub workflow runs the same tests and uploads that
directory.

## Check workloads and databases

Validate every workload command and report duplicate sources:

```sh
PYTHONPATH=src python -m miopen_tuning workloads check
```

Database checks use one exact file pair named from the target device prefix and
MIOpen version, such as `gfx950100.HIP.3_6_3_37f616f6.udb.txt` and
`gfx950100.HIP.3_6_3_37f616f6.ufdb.txt`. A workload problem is already tuned
only when both files contain its records:

```sh
PYTHONPATH=src python -m miopen_tuning db check \
  --db-prefix gfx950100 \
  --miopen-version 3.6.3 \
  --miopen-build 37f616f6 \
  --workloads data/miopen/workloads

PYTHONPATH=src python -m miopen_tuning plan \
  --db-prefix gfx950100 \
  --miopen-version 3.6.3 \
  --miopen-build 37f616f6 \
  --output-dir /tmp/miopen-plan
```

`python -m miopen_tuning identity --output identity.json` records these values
from an image, and the commands accept `--identity identity.json`.

## Run local tuning

`data/miopen/tune.sh` wraps the Python tuning runner. The runner can also be
called directly from the repository root:

```sh
HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 \
PYTHONPATH=src python -m miopen_tuning run \
  --output-dir /tmp/miopen-run \
  --install-db
```

The runner combines workload files, tunes each problem once, writes readable
logs for each command, merges tuned records by key, validates the exact DB pair,
and writes `report.json`. It installs the merged DB only when every command
succeeds and every successful command has both DB records.

## CI tuning reports

CI runs MIOpen contract tests before tuning. Each architecture uploads two
artifacts:

- `<run-number>-miopen-<architecture>` contains only the validated `.udb.txt`
  and `.ufdb.txt` files for the target MIOpen version.
- `miopen-report-<run-number>-miopen-<architecture>` contains the complete run
  directory, including readable MIOpen logs, parsed events, plans, DB diffs,
  `report.md`, and `summary.md`.
