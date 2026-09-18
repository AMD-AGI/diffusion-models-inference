# Build and Benchmark Workflow Inputs

Complete reference for all `build-and-benchmark.yml` workflow_dispatch inputs.

## Build Source Parameters

| Input | Type | Default | Description |
|---|---|---|---|
| `git_branch` | string | `''` | Git branch to build from. Empty uses the repo default branch. |
| `base_image` | string | `''` | Tag or full Docker Hub path to start from. Empty always forces a rebuild. |
| `rebuild` | boolean | `false` | Rebuild the image. Forced on when `base_image` is empty. With `base_image` set, this uses it as a build cache source (bare tag against the core image) instead of using it directly. |

### Build source behavior

```
base_image empty                → rebuild forced on, full build from source
base_image set, rebuild=false   → use base_image directly, no build
base_image set, rebuild=true    → build from source using base_image as cache
```

## Step Checkboxes

| Input | Type | Default | Description |
|---|---|---|---|
| `run_miopen_tuning` | boolean | `true` | Run MIOpen tuning. |
| `run_benchmarks` | boolean | `true` | Run benchmarks. |
| `build_final` | boolean | `true` | Build the final tuned image and push it. |
| `create_miopen_db_pr` | boolean | `false` | Push a `miopen/<run_number>-<run_attempt>` branch with the updated tuning database. The run summary links a prefilled pull request form; the pull request itself is opened manually. |

Each checkbox is independent — there is no implicit run-mode coupling. A
benchmark-only run (no builds, no tuning) is `base_image` set, `rebuild=false`,
`run_miopen_tuning=false`, `build_final=false`, `run_benchmarks=true`.

## MIOpen Configuration

| Input | Type | Default | Description |
|---|---|---|---|
| `miopen_find_mode` | string | `1` | MIOpen find mode (integer). Almost never changed. |
| `miopen_find_enforce` | string | `3` | MIOpen find enforce (integer). Almost never changed. |
| `force_retuning` | boolean | `false` | Delete existing tuning databases before tuning. |

## Benchmark Control

| Input | Type | Default | Description |
|---|---|---|---|
| `benchmark_flags` | string | `''` | Filter which benchmarks to run. Examples: `--tag release`, `--name CONFIG_NAME`. Empty runs all. |
| `collect_hipblaslt_logs` | boolean | `false` | Collect per-process hipBLASLt GEMM YAML logs for each benchmark. |
| `disable_docker_cache` | boolean | `false` | Disable Docker cache when a core image build is required. The cache is still re-exported, so this is how a stale cache gets replaced. |
| `cache_scope` | string | `''` | Layer cache scope. Defaults to the built branch, so only builds of `main` touch the mainline cache. Set it to isolate a test build launched from `main`. |
| `build_runner` | string | `''` | Runner label for the build jobs (core/untuned build, final image build, MIOpen branch). Empty uses the repository default. |

## GPU Runners

| Input | Type | Default | Description |
|---|---|---|---|
| `gpu_runners` | string | `gfx942,gfx950` | Required comma-separated self-hosted runner labels. Each label becomes the matrix job/artifact key and its `runs-on` value. |

The benchmark container detects its actual `gfx*` architecture and adds the
matching benchmark tag unless `benchmark_flags` contains `--name`.

### Overlapping labels

The following user-supplied combinations require explicit confirmation before
dispatch because the model-specific runners also carry the generic label:

- `gfx942` with `mi300`, `mi308`, or `mi325`
- `gfx950` with `mi350` or `mi355`

Keeping both labels creates separate matrix entries. The skill must not silently
deduplicate them.
