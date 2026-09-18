# Notes — trigger-build-and-benchmark

## 1. Image source behavior

```
base_image empty                → rebuild forced on, full build from source
base_image set, rebuild=false   → use base_image directly, no build
base_image set, rebuild=true    → build from source using base_image as cache
```

`base_image` accepts a bare tag (resolved to `amdsiloai/pytorch-xdit-staging:<tag>`)
or a full Docker Hub path when used directly (`rebuild=false`). When used as a
build cache source (`rebuild=true`), it must be a bare tag against the core image.

## 2. Confirmation checklist

When presenting the command, highlight:
- Selected run profile and dispatch ref
- `base_image` and effective rebuild behavior
- Comma-separated GPU runner labels
- Any non-default step checkboxes or overrides
- Whether `create_miopen_db_pr` will push a `miopen/<run_number>-<run_attempt>` branch and link a prefilled pull request form in the run summary

## 3. Validation rules

- `gpu_runners` must contain at least one non-empty comma-separated runner label
- `miopen_find_mode` and `miopen_find_enforce` must be integers
- Boolean values must be `true` or `false`
- `benchmark_flags` must not combine `--name` and `--tag`

## 4. Workflow effects

- Each step checkbox (`rebuild`, `run_miopen_tuning`, `run_benchmarks`, `build_final`,
  `create_miopen_db_pr`) is independent; there is no implicit run-mode coupling.
- `base_image` empty forces `rebuild` on regardless of the checkbox value.
- `create_miopen_db_pr` pushes the branch and links the pull request form only if tuning produced database changes.

## 5. Runner label overlap

Split `gpu_runners` on commas, trim whitespace, and compare labels exactly.
Require a separate confirmation when either combination is present:

- `gfx942` together with any of `mi300`, `mi308`, or `mi325`
- `gfx950` together with either `mi350` or `mi355`

The model-specific runners also carry their corresponding generic architecture
label. Explain that specifying both labels creates separate matrix entries that
may target the same runner class. Ask whether both entries are intentional. If
not, ask the user to keep either the generic label or the model-specific label.

This confirmation is conditional and occurs before the normal final command
confirmation. Do not silently remove or deduplicate user-supplied labels.
