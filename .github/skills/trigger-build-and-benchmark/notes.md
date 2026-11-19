# Notes — trigger-build-and-benchmark

## 1. Image source behavior

The framework defaults to `xdit`; choose `sgld` or `vllmo` explicitly for those
images. The prebuilt-core/cache behavior below applies to xDiT. SGL-D/vLLM-Omni
rebuilds do not consume `base_image` yet and use their Dockerfile base defaults.
For reuse without rebuilding, bare tags resolve against the selected framework's
repository (`STAGING_IMAGE` for xDiT).

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
- Selected framework
- Selected run profile and dispatch ref
- `base_image` and effective rebuild behavior
- Comma-separated GPU runner labels
- Any non-default step checkboxes or overrides
- Whether `create_miopen_db_branch` will push a `miopen/<run_number>-<run_attempt>` branch and link a prefilled pull request form in the run summary

## 3. Validation rules

- `framework` must be `xdit`, `sgld`, or `vllmo`
- `use_xdit_configs=true` requires `framework=xdit`
- `gpu_runners` must contain at least one non-empty comma-separated runner label
- Boolean values must be `true` or `false`
- `benchmark_flags` must not combine `--name` and `--tag`

## 4. Workflow effects

- `build_final` is xDiT-only; other frameworks skip it with a notice. All three
  frameworks support optional tuning, benchmarks, and MIOpen DB branch creation.
- Each step checkbox (`rebuild`, `run_miopen_tuning`, `run_benchmarks`, `build_final`,
  `create_miopen_db_branch`) is independent; there is no implicit run-mode coupling.
- `base_image` empty forces `rebuild` on regardless of the checkbox value.
- `create_miopen_db_branch` pushes the branch and links the pull request form only if tuning produced database changes.

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
