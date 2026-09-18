# Run Profiles

Every profile is expressed as `base_image` + the five step checkboxes
(`rebuild`, `run_miopen_tuning`, `run_benchmarks`, `build_final`,
`create_miopen_db_pr`). There is no run-mode input; combine checkboxes directly.

## Full standard build (default)

- `base_image`: empty (forces rebuild)
- `run_miopen_tuning`: true, `run_benchmarks`: true, `build_final`: true
- `create_miopen_db_pr`: false
- Builds core and untuned images, tunes, benchmarks, then builds the final image

## Prebuilt image as build cache

- `base_image`: set, `rebuild`: true
- Builds from source using `base_image` as a Docker layer cache, then tunes and benchmarks

## Prebuilt image, no rebuild

- `base_image`: set, `rebuild`: false
- Skips all builds, tunes, benchmarks, and builds the final image from `base_image`

## Benchmark only

Runs benchmarks against an existing image. No build steps, no tuning.
- `base_image`: set (tag or full Docker Hub path), `rebuild`: false
- `run_miopen_tuning`: false, `build_final`: false, `run_benchmarks`: true

## MIOpen tuning only

Tunes MIOpen databases without benchmarking or building the final image.
- `run_miopen_tuning`: true, `run_benchmarks`: false, `build_final`: false
- `create_miopen_db_pr`: true

## MIOpen tuning + benchmarking

Tunes then benchmarks, without building the final image.
- `run_miopen_tuning`: true, `run_benchmarks`: true, `build_final`: false
- `create_miopen_db_pr`: true

All profiles require a non-empty `gpu_runners` value. The workflow default is
`gfx942,gfx950`.

