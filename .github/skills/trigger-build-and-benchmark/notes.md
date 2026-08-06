# Notes — trigger-build-and-benchmark

## 1. Image source precedence

```
benchmark_image set         → benchmark-only mode, no builds at all
prebuilt_untuned_image_tag  → skips core + untuned builds
prebuilt_core_image_tag     → skips core build only
none set                    → full build from source
```

`benchmark_image` is mutually exclusive with build parameters.

## 2. Confirmation checklist

When presenting the command, highlight:
- Selected run profile and dispatch ref
- Image source and any prebuilt tag
- Comma-separated GPU runner labels
- Any non-default overrides
- Whether a tuning mode will create a `miopen/<run_number>-<run_attempt>` branch

## 3. Validation rules

- `gpu_runners` must contain at least one non-empty comma-separated runner label
- `benchmark_image` is mutually exclusive with `prebuilt_untuned_image_tag` and `prebuilt_core_image_tag`
- `run_mode` must be `Standard run`, `MIOpen tuning only`, or `MIOpen tuning + benchmarking`
- `miopen_find_mode` and `miopen_find_enforce` must be integers
- Boolean values must be `true` or `false`
- `benchmark_flags` must not combine `--name` and `--tag`

## 4. Workflow effects

- `benchmark_image` skips image builds and MIOpen tuning.
- `MIOpen tuning only` skips benchmarks but still builds the final tuned image after successful tuning.
- Both MIOpen tuning modes create a `miopen/<run_number>-<run_attempt>` branch after successful tuning.
- `Standard run` does not create a MIOpen branch.
