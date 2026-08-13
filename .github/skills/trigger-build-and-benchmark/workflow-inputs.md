# Build and Benchmark Workflow Inputs

Complete reference for all `build-and-benchmark.yml` workflow_dispatch inputs.

## Build Source Parameters

| Input | Type | Default | Description |
|---|---|---|---|
| `git_branch` | string | `''` | Git branch to build from. Empty uses the repo default branch. |
| `prebuilt_core_image_tag` | string | `''` | Tag for a prebuilt core image. Skips core image build only. |
| `prebuilt_untuned_image_tag` | string | `''` | Tag for a prebuilt untuned image. Skips both core and untuned builds. Format: `<short-sha>-temp`. |
| `benchmark_image` | string | `''` | Image for benchmark-only mode. Can be a tag (resolved to `amdsiloai/pytorch-xdit-staging:<tag>`) or full Docker Hub path. Skips ALL builds. |

### Build source precedence

```
benchmark_image set         → benchmark-only mode, no builds at all
prebuilt_untuned_image_tag  → skips core + untuned builds
prebuilt_core_image_tag     → skips core build only
none set                    → full build from source
```

## Run Mode

| Input | Type | Default | Options |
|---|---|---|---|
| `run_mode` | choice | `Standard run` | `Standard run`, `MIOpen tuning only`, `MIOpen tuning + benchmarking` |

- **Standard run**: Build → tune → benchmark → build final image
- **MIOpen tuning only**: Build → tune → create MIOpen DB PR (no benchmarks, no final image)
- **MIOpen tuning + benchmarking**: Build → tune → benchmark → create MIOpen DB PR

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
| `disable_docker_cache` | boolean | `false` | Disable Docker cache when a core image build is required. |

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
