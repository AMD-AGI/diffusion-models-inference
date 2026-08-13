# Run Profiles

## Full standard build

- `run_mode`: `Standard run`
- No prebuilt image fields
- Builds core and untuned images, tunes, benchmarks, then builds the final image

## Prebuilt core build

- `run_mode`: `Standard run`
- `prebuilt_core_image_tag`: required
- Builds the untuned image from the existing core image, then tunes and benchmarks

## Prebuilt untuned build

- `run_mode`: `Standard run`
- `prebuilt_untuned_image_tag`: required
- Skips both initial image builds, then tunes, benchmarks, and builds the final image

## Benchmark only

Runs benchmarks against an existing image. No build steps.
- `benchmark_image`: required (tag or full Docker Hub path)
- `run_mode`: Standard run
- Skips MIOpen tuning and final image creation

## MIOpen tuning only

Tunes MIOpen databases without benchmarking.
- `run_mode`: MIOpen tuning only
- Optional prebuilt image field
- Skips benchmarks, builds the final tuned image, and creates a MIOpen branch

## MIOpen tuning + benchmarking

Tunes then benchmarks.
- `run_mode`: MIOpen tuning + benchmarking
- Optional prebuilt image field
- Tunes, benchmarks, builds the final image, and creates a MIOpen branch

All profiles require a non-empty `gpu_runners` value. The workflow default is
`gfx942,gfx950`.
