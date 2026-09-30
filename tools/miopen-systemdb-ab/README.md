# MIOpen System DB vs Exhaustive Tuning A/B

Detect convolutions where MIOpen's installed **system database** selects a
suboptimal solver compared to **exhaustive tuning** (system DB overridden).

## Quick start

From the repository root, on a machine with AMD GPUs and Docker, build the
ROCm image in [`Dockerfile`](Dockerfile) first, then point the run at that tag.
The image is Ubuntu 24.04 plus the latest ROCm 10.1 pre-release (native
`apt` packages; see the Dockerfile header). It contains MIOpen, HIP, and
rocBLAS for gfx942 and gfx950. The repository is bind-mounted at run time, so
it is not copied into the image.

```bash
docker build \
  -f tools/miopen-systemdb-ab/Dockerfile \
  -t miopen-systemdb-ab:10.1.0-pre3 \
  .

# Copy and edit config if needed (GPU list, repeats, skips, etc.)
cp tools/miopen-systemdb-ab/config.example.env tools/miopen-systemdb-ab/config.env

# Full run (all workload files; GPUs and other settings from config.env)
DOCKER_IMAGE=miopen-systemdb-ab:10.1.0-pre3 \
bash tools/miopen-systemdb-ab/run_experiment.sh
```

`run_experiment.sh` reads `tools/miopen-systemdb-ab/config.env` when present. Each
`KEY=value` line sets a default only if that variable is **not** already exported, so
one-off overrides on the command line still work. Set `DOCKER_IMAGE` in
`config.env` to the tag you built if you do not want to pass it on the command
line. Without that setting the script uses its built-in staging image.

Build a single GPU architecture, or pin another pre-release, with the
Dockerfile `ARG`s:

```bash
docker build \
  -f tools/miopen-systemdb-ab/Dockerfile \
  --build-arg ROCM_GFX_TARGETS=gfx950 \
  --build-arg ROCM_VERSION=10.1.0~pre3-36179538173 \
  -t miopen-systemdb-ab:10.1.0-pre3 \
  .
```

Validate on a small subset first:

```bash
DOCKER_IMAGE=miopen-systemdb-ab:10.1.0-pre3 \
WORKLOADS_GLOB='data/miopen/workloads/flux.single_gpu.txt' \
HIP_VISIBLE_DEVICES=0 \
bash tools/miopen-systemdb-ab/run_experiment.sh
```

### Configuration (`config.env`)

| Variable | Default (if unset) | Description |
|----------|-------------------|-------------|
| `HIP_VISIBLE_DEVICES` | `0` | Comma-separated GPU indices passed into the container |
| `DOCKER_IMAGE` | staging image tag | Image from [`Dockerfile`](Dockerfile). Set this to the tag you built (`miopen-systemdb-ab:10.1.0-pre3`) |
| `WORKLOADS_GLOB` | `data/miopen/workloads/*.txt` | Workload command files |
| `THRESHOLD_PCT` | `2.0` | Report classification threshold |
| `BENCHMARK_REPEATS` | `3` | Timed repetitions per command |
| `OUTPUT_DIR` | `tools/miopen-systemdb-ab/runs/<timestamp>` | Run output directory (must be inside repo) |
| `SKIP_BENCHMARK_A` | `false` | Skip Arm A benchmarks |
| `SKIP_TUNE` | `false` | Skip Arm B exhaustive tuning |
| `SKIP_BENCHMARK_B` | `false` | Skip Arm B post-tune benchmarks |
| `DRY_RUN` | `false` | Print planned phases and exit |

Without `config.env`, only **GPU 0** is used (`HIP_VISIBLE_DEVICES` defaults to `0`).
Copy `config.example.env` to use all eight GPUs listed there.

## Inside the container

```bash
cd /app/diffusion-models-inference
export HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export DOCKER_IMAGE=miopen-systemdb-ab:10.1.0-pre3
export PYTHONPATH=src:tools/miopen-systemdb-ab

python tools/miopen-systemdb-ab/run_experiment.py \
  --output-dir tools/miopen-systemdb-ab/runs/manual_run \
  --threshold-pct 2.0 \
  --benchmark-repeats 3
```

## Experiment arms

| Arm | Description |
|-----|-------------|
| **A** | Out-of-the-box path: `MIOPEN_FIND_ENFORCE=1` (no forced tuning), default find mode, empty user DB, then the installed system DB, then production heuristics when the shape misses the system DB |
| **B** | Exhaustive override: `MIOPEN_FIND_ENFORCE=4` (`SEARCH_DB_UPDATE`), `MIOPEN_FIND_MODE=1`, `MIOPEN_SYSTEM_DB_PATH=$MIOPEN_USER_DB_PATH`, then time the merged user DB with `MIOPEN_FIND_ENFORCE=1` and the default find mode |

Arm A matches a fresh install. MIOpen starts from an empty user DB created for the
run (`arm_a/user_db`), then the installed system DB, then heuristics
(`DYNAMIC_HYBRID`) when the shape is not in the system DB. It does not load
`data/miopen/userdb` or `/miopen_userdb`, and it does not run incremental inline
tuning. Arm B is the exhaustive-tuned upper bound for the same shapes: tuning uses
`SEARCH_DB_UPDATE` so the winner is written, and the follow-up benchmark times that
merged user DB instead of searching again.

Each command is timed **3 times**; the report uses the **median**.

All arms set **`MIOPEN_DEBUG_CONV_DIRECT=0`** by default so expensive naive direct
convolution solvers are excluded from find/tune (same as `data/miopen/tune.sh`).

Arm A writes nothing into the repository user DB. `MIOPEN_USER_DB_PATH` is
`arm_a/user_db`, an empty directory created under the run output.

## Where results are saved (host)

When you use `run_experiment.sh`, the repository root is **bind-mounted** into the
container. Everything written under `--output-dir` lands on the **host filesystem**
inside your checkout — nothing is lost when the container exits.

Default location after a run:

```
tools/miopen-systemdb-ab/runs/<timestamp>/
```

The container runs as root (required for ROCm GPU access) but **`run_experiment.sh`
passes your `HOST_UID` and `HOST_GID` into the container**. Before exit,
`run_experiment.py` runs `chown -R` on the output directory (same approach as
`data/miopen/tune.sh`), so all artifacts belong to the user who invoked the
script — not root.

Override with `OUTPUT_DIR` (must stay inside the repo):

```bash
DOCKER_IMAGE=miopen-systemdb-ab:10.1.0-pre3 \
OUTPUT_DIR=tools/miopen-systemdb-ab/runs/my_mi350_run \
HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 \
bash tools/miopen-systemdb-ab/run_experiment.sh
```

After completion, the script prints the host path. Open `artifacts.json` in the
run directory for a full list of persisted files including user DB paths.

## Distributed execution and database merging

Benchmark and tuning tasks are distributed across GPUs using
[`src/distrituner`](../../src/distrituner/) (same mechanism as `miopen_tuner.py`):

1. `run_experiment.sh` passes `HIP_VISIBLE_DEVICES` into the container (`-e`).
2. `run_experiment.py` reads that value (or `--gpus`) and splits it into one device
   ID per worker via `get_device_ids()`.
3. Each worker process sets `HIP_VISIBLE_DEVICES` to a **single** index from that
   list (e.g. container env `0,1,2,3` → workers pinned to `0`, `1`, `2`, `3`).
4. Tasks (MIOpenDriver commands) are queued and assigned to idle workers; up to
   one command per GPU at a time.
5. Per-task stdout/stderr is saved under `arm_*/logs/` or `arm_b/tune_logs/`.

**Arm A (out-of-the-box path):** all workers share one empty user DB at
`arm_a/user_db`. No prebuilt user DB is loaded. `MIOPEN_FIND_ENFORCE=1` does not
write tuning results. `MIOPEN_PERFORMANCE_LOGS=1` records the kernel instance
that ran. Raising find-enforce to write the user DB would force a full search
and would no longer be the production path.

**Arm B (exhaustive tuning):** each worker writes to its own directory
(`arm_b/tuning/device_<gpu_id>/`) with `MIOPEN_SYSTEM_DB_PATH` set equal to
`MIOPEN_USER_DB_PATH` so the system DB is ignored. After tuning completes,
`merge_tuning_databases()` concatenates all per-device `.udb.txt` and `.ufdb.txt`
files, deduplicates lines, and writes the merged DB to `arm_b/tuning_merged/`.
Both the per-device and merged directories are kept on disk for inspection.

**Arm B benchmark:** all workers read from the merged DB at `arm_b/tuning_merged/`.

## Outputs

Each run writes to `tools/miopen-systemdb-ab/runs/<run_id>/`:

| File | Description |
|------|-------------|
| `report.md` | Summary of shapes that ran a different kernel, plus per-workload counts |
| `report.json` | Same summary, with full (untruncated) different-solver lists |
| `comparison.json` | Full per-command record, including same-solver timings, repeat times, GPU ids, and parsed shape |
| `arm_b/tune_devices.json` | GPU that ran exhaustive search for each command |
| `metadata.json` | GPU, ROCm, MIOpen versions (+ artifact paths after completion) |
| `artifacts.json` | Manifest of all persisted paths, including user DB files |
| `arm_a/results.jsonl` | Arm A timings |
| `arm_b/results.jsonl` | Arm B timings |
| `arm_b/tuning/device_*/` | Per-GPU exhaustive tuning DBs (pre-merge) |
| `arm_b/tuning_merged/*.udb.txt` | Merged exhaustive user performance DB |
| `arm_b/tuning_merged/*.ufdb.txt` | Merged exhaustive user find DB |

## Classification

Configurable via `--threshold-pct` (default 2%). `comparison.json` keeps the full
record for every command: parsed shape, repeat times, both solver configs,
`delta_ms` (Arm A − Arm B), `source_files`, and `in_system_db`. `report.md`
shortens solver names and groups those records.

The report compares kernel instances, not only solver names. The instance is
the perf config after `:`. A matching name is the same kernel only for
single-kernel solvers such as `GemmFwdRest`. ImplicitGEMM CK solvers
(`ConvHipImplicitGemm*`) and dynamic IGEMM solvers
(`ConvAsmImplicitGemmGTCDynamic*`) each cover many kernels, so those rows stay
in the tables when the configs differ. When a multi-kernel solver ran and the
config was not recorded, the row is reported as **kernel not recorded** instead
of being dropped as noise. Benchmark arms set `MIOPEN_PERFORMANCE_LOGS=1` so
the executed config is captured without changing find mode. Tables and the
"milliseconds left on the table" sum cover every shape that is not the same
kernel, split by whether exhaustive was faster, similar (within the threshold),
or slower. Each of those rows includes the GPUs that produced the benchmark
repeats (`arm_a_device_ids`, `arm_b_device_ids`) and, when tuning ran in this
process, the GPU that wrote the exhaustive solution (`arm_b_tune_device`).

`parity` is still stored on every entry as the raw timing comparison:

- **equal** — medians within the threshold
- **production_slower** — exhaustive median is faster by more than the threshold
- **exhaustive_slower** — exhaustive median is slower by more than the threshold
- **failed** — driver failure or an incomplete timing

`outcome` is still recorded. **improvement** matches `production_slower`.
**regression** is the exhaustive-slower case where the solver or the kernel
instance changed. **no_change** is everything else that timed successfully,
including exhaustive-slower on the same kernel. **system_db_miss** is not an outcome:
`in_system_db` is false when the shape is absent from the installed system
performance DB (`{prefix}.db.txt`, or a legacy `{prefix}*.udb.txt`). Those
shapes are still compared.

## Resume / partial runs

Re-running the same `--output-dir` skips benchmark repetitions already recorded
in `results.jsonl`.

Skip phases when iterating:

```bash
python tools/miopen-systemdb-ab/run_experiment.py \
  --output-dir tools/miopen-systemdb-ab/runs/manual_run \
  --skip-tune --skip-benchmark-a   # compare/report only (needs prior results)
```

## Tests

```bash
source .venv/bin/activate
PYTHONPATH=src:tools/miopen-systemdb-ab pytest tools/miopen-systemdb-ab/tests -v
```

## Note on deployment

The Python package lives in `tools/miopen-systemdb-ab/miopen_ab/` (not `lib/` — that
name is reserved by the repo-root `.gitignore` for virtualenv directories). Ensure
this directory is present on the machine running Docker before invoking
`run_experiment.sh`.
