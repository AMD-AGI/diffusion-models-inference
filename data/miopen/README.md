# MIOpen user databases

This directory holds the MIOpen user databases (DBs) that ship in the CI images,
and the MIOpenDriver commands they are tuned for. With these DBs, MIOpen uses
tuned convolution kernels for the benchmarked workloads instead of its default
heuristics.

- `userdb/`: a find DB (`.ufdb.txt`) and a performance DB (`.udb.txt`) for each
  device and MIOpen version, for example
  `gfx950100.HIP.3_6_3_37f616f6.ufdb.txt`.
- `workloads/`: MIOpenDriver commands collected from benchmark runs, one file
  per workload.
- `tune.sh`: runs tuning inside an image.

The final image copies `userdb/` to `/miopen_userdb` and sets
`MIOPEN_USER_DB_PATH`. To use the DBs elsewhere, point `MIOPEN_USER_DB_PATH` at
`userdb/`.

MIOpen reads only the DB pair whose name matches the GPU and the installed
MIOpen. The name starts with the device prefix, which is the gfx architecture
followed by the compute unit count in hexadecimal: an MI300X or MI325X
(`gfx942`, 304 compute units) uses `gfx942130`, and an MI350X or MI355X
(`gfx950`, 256 compute units) uses `gfx950100`. The tuning tools compute the
prefix from `rocminfo`, so a new GPU needs no configuration. The rest of the
name is the MIOpen version and build, so a ROCm update that changes MIOpen
needs new DBs.

## Update the databases

1. [Collect workload commands](#collect-workload-commands) from a benchmark
   run and import the changes into `workloads/`.
2. [Tune in CI](#tune-in-ci) with the Build and Benchmark workflow.
3. Review the tuning summary and report.
4. Merge the DB branch that the workflow creates.

## Collect workload commands

The Build and Benchmark workflow logs the MIOpenDriver command of every
convolution in every benchmark run. It uploads one artifact per architecture
named `<run-number>-collected-miopen-workloads-<architecture>`. The name must
not match `<run-number>-miopen-*`, the pattern that downloads the tuned DBs.
Each artifact contains:

- `<workload>.txt` for a benchmark process that exited successfully.
- `<workload>.partial.txt` for commands captured before a benchmark process
  exited unsuccessfully.
- `manifest.json`, which records every selected workload, its benchmark and
  collection statuses, its unique command count, and its output filename.
  Workloads that emitted no commands have no text file but remain in the
  manifest.

Commands are sorted and de-duplicated within each workload. Duplicates across
workloads are kept; the tuning planner tunes each problem once and records
every workload that uses it.

Compare a downloaded artifact with the workload files:

```sh
PYTHONPATH=src python -m miopen_tuning workloads import \
  /tmp/123-collected-miopen-workloads-gfx950 \
  --output /tmp/miopen-workload-import.json
```

The report lists added, removed, and unchanged commands for each workload and
architecture. It flags `.partial.txt` files and rejects invalid commands. Add
`--write` to update the workload files. Workload files are named
`<workload>.txt`, or `<workload>.<architecture>.txt` when the commands differ
by architecture; the import updates the architecture file when it exists.
Use `--include-partial` only after reviewing commands from failed benchmark
runs.

To collect commands when running `.ci/run.py` yourself, set
`CI_RUN_PY_COLLECT_MIOPEN_DRIVER_COMMANDS=1`. From a repository checkout, also
set `PYTHONPATH=src`.

## Tune in CI

Run the Build and Benchmark workflow with these inputs:

- `run_miopen_tuning`: tune on each architecture in `gpu_runners`.
- `force_retuning`: tune every problem from an empty DB. Without it, tuning is
  incremental: problems that already have both DB records are skipped, and
  MIOpen runs with `MIOPEN_FIND_ENFORCE=3`, which searches only problems
  without a performance record.
- `create_miopen_db_branch`: push the tuned DBs to a
  `miopen/<run-number>-<attempt>` branch and link a prefilled pull request
  form in the job summary.
- `run_benchmarks` and `build_final`: turn these off for a tuning-only run.
  When the final image is built, it uses the DBs tuned in the same run.

Each tuning job first runs the [MIOpen contract tests](#test-the-tuning-tools).
It then tunes the planned problems, merges the results into the DB pair by
record key, and validates the merged pair. The job summary shows the result,
and each architecture uploads two artifacts:

- `<run-number>-miopen-<architecture>`: the validated `.udb.txt` and
  `.ufdb.txt` files.
- `miopen-report-<run-number>-miopen-<architecture>`: the complete run
  directory, including readable MIOpen logs for each command, parsed events,
  the plan, DB diffs, `report.md`, and `summary.md`.

The job fails when a tuning command fails or a successful command is missing a
DB record. Two cases are reported as warnings instead:

- Perf records from the system DB: with `MIOPEN_FIND_ENFORCE=3`, MIOpen does
  not search a problem whose performance record is already in the system DB
  shipped with ROCm, so it writes no user record. Such problems are planned
  again on every run. The re-run is short because MIOpen loads the system
  record instead of searching.
- Find records not written: MIOpen sometimes completes a find without writing
  the find DB record, together with an `IsEnoughWorkspace` warning in the log.
  The summary lists these commands with their logs.

## Tune locally

Run `tune.sh` in an image that contains ROCm and MIOpenDriver, such as a
staging image from CI or the `core` target of `docker/Dockerfile.ci`:

```sh
docker build -f docker/Dockerfile.ci --target core -t pytorch_xdit_core .

docker run --rm \
  --security-opt seccomp=unconfined \
  --device=/dev/kfd --device=/dev/dri \
  --group-add video \
  --shm-size 128G \
  --mount type=bind,src="$PWD",dst=/app/diffusion-models-inference \
  -w /app/diffusion-models-inference \
  -e HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 \
  pytorch_xdit_core \
  bash data/miopen/tune.sh
```

`tune.sh` tunes the workloads into `data/miopen/userdb`, writes the run
directory to `miopen-run/`, and creates `.tuning_successful` when the run
passes. It installs the merged DB only when every command succeeds and every
successful command has both DB records. Set `FORCE_RETUNING=true` to tune every
problem from an empty DB.

The runner can also be called directly from the repository root:

```sh
HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 \
PYTHONPATH=src python -m miopen_tuning run \
  --output-dir /tmp/miopen-run \
  --install-db
```

The runner uses the GPUs in `--devices` or `HIP_VISIBLE_DEVICES`, or GPU 0
when neither is set, and runs one command per GPU at a time.

## Check workloads and databases

Validate every workload command and report duplicate sources:

```sh
PYTHONPATH=src python -m miopen_tuning workloads check
```

Check which workload problems the DB pair for a device and MIOpen version
covers, and write the tuning plan. A problem counts as tuned only when both DB
files contain its records:

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

`python -m miopen_tuning identity --output identity.json` records the device
prefix and MIOpen version of an image, and these commands accept
`--identity identity.json` instead of the three options. Problems served by
the system DB appear in `find_without_perf` from `db check --require-complete`.

## Test the tuning tools

The tuning tools have CPU-only tests that run without a GPU, ROCm image, or
MIOpen installation:

```sh
python -m pip install pytest
PYTHONPATH=src python -m pytest -q src/miopen_tuning/tests
```

GPU contract tests check the MIOpen behavior that the tuning tools depend on,
such as DB names, record keys, and log formats. Run them inside the image being
tested:

```sh
MIOPEN_CONTRACT_TESTS=1 \
MIOPEN_CONTRACT_ARTIFACTS=/tmp/miopen-contracts \
PYTHONPATH=src \
python -m unittest -v miopen_tuning.tests.miopen.test_miopen_contracts
```

The artifact directory must be new or empty. It receives readable `stdout.log`
and `stderr.log` files, parsed `events.jsonl`, metadata, tuned DBs, and the
image identity. The manual `MIOpen contracts` workflow runs the same tests and
uploads that directory.
