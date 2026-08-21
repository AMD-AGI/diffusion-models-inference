# MIOpen System DB vs Exhaustive Tuning A/B

Detect convolutions where MIOpen's installed **system database** selects a
suboptimal solver compared to **exhaustive tuning** (system DB overridden).

## Quick start

From the repository root, on a machine with AMD GPUs and Docker:

```bash
# Copy and edit config if needed (GPU list, repeats, skips, etc.)
cp tools/miopen-systemdb-ab/config.example.env tools/miopen-systemdb-ab/config.env

# Full run (all workload files; GPUs and other settings from config.env)
bash tools/miopen-systemdb-ab/run_experiment.sh
```

`run_experiment.sh` reads `tools/miopen-systemdb-ab/config.env` when present. Each
`KEY=value` line sets a default only if that variable is **not** already exported, so
one-off overrides on the command line still work.

Validate on a small subset first:

```bash
WORKLOADS_GLOB='data/miopen/workloads/flux.single_gpu.txt' \
HIP_VISIBLE_DEVICES=0 \
bash tools/miopen-systemdb-ab/run_experiment.sh
```

### Configuration (`config.env`)

| Variable | Default (if unset) | Description |
|----------|-------------------|-------------|
| `HIP_VISIBLE_DEVICES` | `0` | Comma-separated GPU indices passed into the container |
| `DOCKER_IMAGE` | staging image tag | Container image for ROCm / MIOpen |
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
export DOCKER_IMAGE=amdsiloai/pytorch-xdit-staging:1cdf53a-temp
export PYTHONPATH=src:tools/miopen-systemdb-ab

python tools/miopen-systemdb-ab/run_experiment.py \
  --output-dir tools/miopen-systemdb-ab/runs/manual_run \
  --threshold-pct 2.0 \
  --benchmark-repeats 3
```

## Experiment arms

| Arm | Description |
|-----|-------------|
| **A** | Production-like path: `MIOPEN_FIND_ENFORCE=3`, default find mode, system DB enabled, inline timing |
| **B** | Exhaustive override: `MIOPEN_FIND_ENFORCE=3`, `MIOPEN_SYSTEM_DB_PATH=$MIOPEN_USER_DB_PATH`, then benchmark merged user DB |

Each command is timed **3 times**; the report uses the **median**.

All arms set **`MIOPEN_DEBUG_CONV_DIRECT=0`** by default so expensive naive direct
convolution solvers are excluded from find/tune (same as `data/miopen/tune.sh`).

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
   one command runs per GPU at a time.
5. Per-task stdout/stderr is saved under `arm_*/logs/` or `arm_b/tune_logs/`.

**Arm A (production path):** all workers share one user DB directory
(`arm_a/user_db/`). Shapes missing from the system DB may be tuned inline and
appended there.

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
| `report.md` | Human-readable bug-ticket report |
| `report.json` | Structured summary |
| `comparison.json` | Full per-command comparison |
| `metadata.json` | GPU, ROCm, MIOpen versions (+ artifact paths after completion) |
| `artifacts.json` | Manifest of all persisted paths, including user DB files |
| `arm_a/results.jsonl` | Arm A timings |
| `arm_b/results.jsonl` | Arm B timings |
| `arm_a/user_db/*.udb.txt` | Arm A user performance DB (inline tuning side effects) |
| `arm_a/user_db/*.ufdb.txt` | Arm A user find DB |
| `arm_b/tuning/device_*/` | Per-GPU exhaustive tuning DBs (pre-merge) |
| `arm_b/tuning_merged/*.udb.txt` | Merged exhaustive user performance DB |
| `arm_b/tuning_merged/*.ufdb.txt` | Merged exhaustive user find DB |

## Classification

Configurable via `--threshold-pct` (default 2%):

- **improvement** — exhaustive median faster → system DB suboptimal
- **no_change** — within threshold
- **regression** — exhaustive slower **and** solver changed
- **system_db_miss** — shape not in system DB (excluded from primary A/B)
- **failure / arch_mismatch_or_error** — driver failure

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
