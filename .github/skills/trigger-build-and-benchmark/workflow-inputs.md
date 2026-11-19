# Build and Benchmark Workflow Inputs

Complete reference for all `build-and-benchmark.yml` workflow_dispatch inputs.

## Build Source Parameters

| Input | Type | Default | Description |
|---|---|---|---|
| `framework` | choice | `xdit` | Framework to build and benchmark: `xdit`, `sgld`, or `vllmo`. |
| `git_branch` | string | `''` | Git branch to build from. Empty uses the repo default branch. |
| `base_image` | string | `''` | Tag or full Docker Hub path to start from. Empty always forces a rebuild. |
| `image_tag` | string | `''` | Override the built image tag. Empty uses the checked-out repository short SHA. |
| `rebuild` | boolean | `false` | Rebuild the selected framework image. Forced on when `base_image` is empty. For xDiT, `base_image` can supply a prebuilt core image; SGL-D/vLLM-Omni builds do not consume this input yet. |

### Build source behavior

```
base_image empty                → rebuild forced on, full build from source
base_image set, rebuild=false   → use base_image directly, no build
base_image set, rebuild=true    → rebuild the selected framework
```

For xDiT, a supplied `base_image` remains the prebuilt core input. SGL-D and
vLLM-Omni rebuilds use their Dockerfile base defaults; forwarding `base_image`
to those builds is not implemented. Bare tags used without rebuilding resolve
against the selected framework's repository (`STAGING_IMAGE` for xDiT).

## Step Checkboxes

| Input | Type | Default | Description |
|---|---|---|---|
| `run_miopen_tuning` | boolean | `true` | Run MIOpen tuning. |
| `run_benchmarks` | boolean | `true` | Run benchmarks. |
| `build_final` | boolean | `true` | Build and push the final tuned xDiT image. Skipped with a notice for `sgld` and `vllmo`. |
| `create_miopen_db_branch` | boolean | `false` | Push a `miopen/<run_number>-<run_attempt>` branch with the updated tuning database. The run summary links a prefilled pull request form; the pull request itself is opened manually. |

Each checkbox is independent — there is no implicit run-mode coupling. A
benchmark-only run (no builds, no tuning) is `base_image` set, `rebuild=false`,
`run_miopen_tuning=false`, `build_final=false`, `run_benchmarks=true`.

## MIOpen Configuration

| Input | Type | Default | Description |
|---|---|---|---|
| `force_retuning` | boolean | `false` | Delete existing tuning databases before tuning. |

## Benchmark Control

| Input | Type | Default | Description |
|---|---|---|---|
| `benchmark_flags` | string | `''` | Filter which benchmarks to run. Examples: `--tag release`, `--name CONFIG_NAME`. Empty runs all. |
| `use_xdit_configs` | boolean | `false` | xDiT-only: select `/app/.ci/benchmark_configs_xdit/`, copied from the tested xDiT commit's `.ci/configs/`, instead of downstream configs. Rejected for `sgld`/`vllmo`. Missing configs or an empty selection fail; no fallback. Does not change MIOpen tuning workloads. |
| `collect_hipblaslt_logs` | boolean | `false` | Collect per-process hipBLASLt GEMM YAML logs for each benchmark. |
| `force_determinism_check` | boolean | `false` | Force xFuser determinism checks for every benchmark. Each architecture is checked in its own `<arch> determinism check` job; a non-deterministic benchmark fails that job, so the run goes red while `build-final` and the MIOpen branch still complete. |
| `disable_docker_cache` | boolean | `false` | Disable Docker cache when a core image build is required. The cache is still re-exported, so this is how a stale cache gets replaced. |
| `cache_scope` | string | `''` | Layer cache scope. Defaults to the built branch, so only builds of `main` touch the mainline cache. Set it to isolate a test build launched from `main`. |
| `build_runner` | string | `''` | Runner label for the build jobs (core/untuned build, final image build, MIOpen branch). Empty uses the repository default. |
| `docker_build_args` | string | `''` | Newline-separated Docker build arguments in `KEY=VALUE` form. |

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
