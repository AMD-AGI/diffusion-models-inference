# MIOpen A/B Run Output Guide

Each run directory (`tools/miopen-systemdb-ab/runs/<timestamp>/`) is the full artifact from one **system DB vs exhaustive tuning** experiment. The experiment benchmarks the same convolution shapes twice:

| Arm | What it measures |
|-----|------------------|
| **A** | Out-of-the-box path — empty user DB, installed system DB, then heuristics |
| **B** | Upper bound — exhaustive tuning (system DB ignored), then benchmark with the merged tuned DB |

Each shape is timed **3 times** by default; reports use the **median** latency.

**Start here:** open `report.md` for the human-readable summary. Use this guide to understand the rest.

---

## Top-level files

| File | Purpose |
|------|---------|
| `report.md` | Summary by workload file, then tables for equal, production-slower, and exhaustive-slower shapes. Solver names in the tables are shortened |
| `report.json` | Same summary as structured JSON, with the full untruncated entries for those three lists |
| `comparison.json` | Ground truth: every shape, full command, parsed shape, repeat times, solver configs |
| `metadata.json` | Run environment: GPU, ROCm/MIOpen versions, Docker image, config |
| `artifacts.json` | Index of every persisted path in the run |
| `commands.txt` | All `MIOpenDriver` commands benchmarked in this run |

---

## Key fields in comparisons

For each convolution command (a line in `commands.txt`):

| Field | Meaning |
|-------|---------|
| `shape` | Parsed convolution (batch, channels, spatial size, kernel, direction, precision, layout) |
| `source_files` | Workload files that contributed this command |
| `arm_a_median_ms` / `arm_b_median_ms` | Median kernel time (milliseconds) |
| `arm_a_times_ms` / `arm_b_times_ms` | Individual timed repeats |
| `delta_ms` | `A − B`. Positive means the production path is slower |
| `speedup_pct` | `(A − B) / A × 100` — positive means B is faster |
| `parity` | `equal`, `production_slower`, `exhaustive_slower`, or `failed` |
| `arm_a_solver` / `arm_b_solver` | Full MIOpen solver selected in each arm, including kernel config when known |
| `system_db_solver` | Solver recorded in the installed system performance DB for this shape (if any) |
| `in_system_db` | Whether the shape exists in that system DB |
| `outcome` | Older classification (see below). Prefer `parity` for equal vs poor |
| `notes` | Extra context (e.g. parse errors, missing system DB entry) |

---

## Outcome classifications

Default threshold: **2%** relative median difference (`threshold_pct` in metadata/comparison).

| `parity` | Meaning |
|----------|---------|
| `equal` | Medians within the threshold |
| `production_slower` | Exhaustive tuning is faster than the out-of-the-box path by more than the threshold |
| `exhaustive_slower` | Exhaustive tuning is slower by more than the threshold |
| `failed` | Driver error, incomplete timings, or architecture mismatch |

`outcome` is also stored: `improvement` matches `production_slower`, `regression` is exhaustive-slower with a different solver, and `no_change` covers equal plus exhaustive-slower with the same solver. `in_system_db` is separate from both. A miss does not replace the timing comparison. The system DB file is `{prefix}.db.txt` on ROCm 10.1, or a legacy `{prefix}*.udb.txt`.

---

## Subdirectories

```
<run>/
├── arm_a/
│   ├── results.jsonl      # Per-command timings (Arm A)
│   └── logs/              # MIOpenDriver stdout/stderr per benchmark
└── arm_b/
    ├── results.jsonl      # Per-command timings (Arm B, post-tune)
    ├── logs/              # Post-tune benchmark logs
    ├── tune_logs/         # Exhaustive tuning logs
    ├── tuning/
    │   └── device_<gpu>/  # Per-GPU tuning DBs before merge
    └── tuning_merged/     # Merged exhaustive user DB (*.udb.txt, *.ufdb.txt)
```

### `results.jsonl` (one JSON object per line)

| Field | Meaning |
|-------|---------|
| `command` | Full `MIOpenDriver` invocation |
| `times_ms` | Individual timed runs |
| `median_ms` / `stddev_ms` | Aggregated statistics |
| `algorithm_ids` / `solver_hints` | Solver info from driver output |
| `returncodes` | Exit codes per repeat |
| `log_files` | Paths to detailed logs |

### Database files

- **`.udb.txt`** — user performance database (solver + timing hints per shape)
- **`.ufdb.txt`** — user find database (which solvers to try)

Arm B tuning writes per-GPU DBs under `arm_b/tuning/device_*/`, then merges them into `arm_b/tuning_merged/` for the final benchmark.

---

## How to read `report.md` tables

| Column | Meaning |
|--------|---------|
| Workload | Basename of the workload file that listed the shape |
| Shape | Short form of the convolution (`n c HxW k kernel direction precision`) |
| Arm A (ms) | Out-of-the-box median latency |
| Arm B (ms) | Exhaustive-tuned median latency |
| Delta (ms) | Arm A − Arm B. Positive means production is slower |
| Speedup | Percent improvement of B over A |
| Arm A / Arm B solver | Shortened solver name. The full config is in `comparison.json` |

**Production slower than exhaustive** is the list of shapes where the out-of-the-box path leaves time on the table. **Equal** is the list that already matches exhaustive tuning within the threshold. Per-file counts and the summed milliseconds are in **By workload file**.
