# Dockerfiles for xDiT diffusion models
- `Dockerfile.ci` docker file for AMD images
- `Dockerfile.cuda` docker file for CUDA images
- `Dockerfile.rdna4` docker file for RDNA4 AMD images
- `Dockerfile.sgld` SGL-D image based on `amdsiloai/pytorch-xdit`, with separate SGL kernel
  libraries for gfx942 and gfx950. [patches/sglang-rocm-arch-loader.patch](patches/sglang-rocm-arch-loader.patch)
  preserves explicit build targets and makes SGLang select the matching library at runtime.
- `Dockerfile.sgld.cuda` SGL-D image for CUDA based on `lmsysorg/sglang`
- `Dockerfile.sgld_lmsys` SGL-D image based on `lmsysorg/sglang-rocm` with CI scripts from xDiT
- `Dockerfile.vllm_omni` vLLM-Omni image for ROCm based on `amdsiloai/pytorch-xdit`
- `Dockerfile.vllm_omni.cuda` vLLM-Omni image for CUDA based on `vllm/vllm-openai`

## How-tos

### Build framework images in CI

Pushes to `main` build xDiT only, with the same path exclusions as before.
SGL-D and vLLM-Omni images are built only by manual dispatch. Use the
`framework` dropdown with `workflow_dispatch` to build one framework:

| Workflow | Framework | Dockerfile | Image variable (default repository) |
| --- | --- | --- | --- |
| [build-core-image.yml](../.github/workflows/build-core-image.yml) | xDiT | `Dockerfile.ci` | `CORE_IMAGE` (`amdsiloai/pytorch-xdit-core`) |
| [build-core-image.yml](../.github/workflows/build-core-image.yml) | SGL-D | `Dockerfile.sgld` | `SGLD_IMAGE` (`amdsiloai/pytorch-xdit-sglang`) |
| [build-core-image.yml](../.github/workflows/build-core-image.yml) | vLLM-Omni | `Dockerfile.vllm_omni` | `VLLM_OMNI_IMAGE` (`amdsiloai/pytorch-xdit-vllm`) |
| [build-nv-image.yml](../.github/workflows/build-nv-image.yml) | xDiT | `Dockerfile.cuda` | `NV_IMAGE` (`amdsiloai/pytorch-xdit-nvidia`) |
| [build-nv-image.yml](../.github/workflows/build-nv-image.yml) | SGL-D | `Dockerfile.sgld.cuda` | `SGLD_NV_IMAGE` (`amdsiloai/pytorch-xdit-sglang-nvidia`) |
| [build-nv-image.yml](../.github/workflows/build-nv-image.yml) | vLLM-Omni | `Dockerfile.vllm_omni.cuda` | `VLLM_OMNI_NV_IMAGE` (`amdsiloai/pytorch-xdit-vllm-nvidia`) |

All SGL-D and vLLM-Omni image repositories are private.

The tag defaults to the short commit SHA; override it with the `tag` input.

Both workflows call the existing reusable build for each selected image.
Manual dispatch builds only the selected framework (xDiT is the default).
xDiT alone builds the untuned image and runs MIOpen tuning. The ROCm images
use separate cache channels scoped to the branch (override with `cache_scope`),
with cache export enabled on pushes or when `export_docker_cache` is set. CUDA
builds produce `-amd64` and `-arm64` tags, then assemble a multi-architecture
manifest at the unsuffixed tag.

Both reusable workflows build private candidates, scan them with Trivy, and
promote them only after scanning passes. Each image has its own cache channel;
the existing xDiT defaults are unchanged. They use `BUILDCACHE_IMAGE` and the
existing `DOCKERHUB_ORGANIZATION` / `DOCKERHUB_OAT` secrets.

The `runner` input overrides `BUILD_RUNNER` (ROCm) or `BUILD_RUNNER_NV`
(CUDA), falling back to `build-only`. `prebuilt_core_image_tag`,
`force_trigger_tuning`, and `gpu_runners` apply only to xDiT. CUDA arm64 builds
require the same pre-registered QEMU handler as the xDiT NV workflow.
Use `disable_docker_cache` for a clean build. Set `docker_build_args` to
newline-separated `KEY=VALUE` entries to override Dockerfile arguments, for
example `BASE_IMAGE=amdsiloai/pytorch-xdit:<tag>` or a dependency commit.
The SGL-D and vLLM-Omni ROCm builds use their Dockerfiles' prebuilt base images;
they do not rebuild the xDiT core or trigger MIOpen tuning.

### PR benchmark configurations from xDiT

`Dockerfile.ci` keeps downstream benchmark configs in
`/app/.ci/benchmark_configs/`. If the tested `XDIT_COMMIT` contains
`.ci/configs/`, its entire contents are also copied to
`/app/.ci/benchmark_configs_xdit/`. Older xDiT commits without that directory
still build normally.

The `use_xdit_configs` workflow/action input defaults to `false`. Set it to
`true` to select only the xDiT configs; `benchmark_flags` still filters with
`--name` or `--tag`. Missing YAML files or an empty upstream selection fail
explicitly, without falling back to downstream configs. Both direct execution
and brr experiment discovery/execution use the selected source.

Configs use this repository's benchmark YAML schema. Copying neighboring assets
preserves directory contents but does not change the runner's working directory
(`/app`); relative argument paths must account for that. Tag selections also
require the runner's architecture tag, such as `gfx942` or `gfx950`.
MIOpen tuning continues to use downstream workload files, independently of this
benchmark config choice.

### ROCm nightly packages

`Dockerfile.ci` installs TheRock nightly debs from the
[Ubuntu 24.04 native index](https://nightly.repo.amd.com/rocm/core/packages/ubuntu2404/).
`ROCM_RELEASE_ID` pins the snapshot and defaults to `20261108-42427827092`.
The package series is derived from the snapshot's `amdrocm-runtime-dev`
metadata. The scheduled nightly workflow selects the latest snapshot from
this same index.
`ROCM_GFX_TARGETS` controls which architecture-specific package shards are
installed. `PYTORCH_ROCM_ARCH` controls the architectures built into PyTorch
and related wheels; it defaults to `ROCM_GFX_TARGETS` but can be overridden
independently.

Leaf packages install under `/opt/rocm/core-<series>`. `rocm_devel` registers
the unversioned `/opt/rocm/{bin,lib,include,...}` links that the
`amdrocm-core` postinst would have created, and keeps the LLVM static
archives those packages install.

| Target | Packages | Use |
| --- | --- | --- |
| `rocm_devel` | HIP/`hipcc`, BLAS, MIOpen, RCCL, FFT, RAND, sparse, and solver for each arch in `ROCM_GFX_TARGETS`, plus headers, developer-tools, RDC, OpenCL, and blas/rccl tests. LLVM static archives kept | Parent of `torch_build_base`, `build_mori`, and `core` |

`amdrocm-developer-tools` pulls the profiler, emulator, and debugger. It does
not depend on `amdrocm-core`. `torch_build_base` fetches TheRock.
`build_triton` and `build_pytorch` compile in series, then `build_vision`
and `build_audio` compile in parallel. `build_torch_stack`
gathers those wheels. `core` installs the wheels built by `build_torch_stack`.
`build_mori` builds the MORI wheel from `rocm_devel` without UMBP, and `core`
installs it after AITER; MORI provides the symmetric memory behind AITER's
intranode attention A2A.
`deps` installs rocprofiler-compute's Python requirements.

```sh
# pinned native Ubuntu 24.04 snapshot
docker build -f docker/Dockerfile.ci --target final -t pytorch-xdit-dev .

# override the native Ubuntu 24.04 snapshot
docker build -f docker/Dockerfile.ci --target final \
    --build-arg ROCM_RELEASE_ID=20261013-38325106112 \
    -t pytorch-xdit-native .
```

The image does not build ROCm from source and does not support
`rocm-libraries` or `rocm-systems` commit overrides. Changes that are not
available in the pinned nightly must first be published in a nightly snapshot.

### amd-smi Python bindings

`docker/setup_amdsmi.sh` links the ROCm Python bindings into the venv, because
the nightly debs ship them under `${ROCM_HOME}/share/amd_smi/amdsmi` with no
`setup.py`, where pip cannot install them and nothing can import them. It runs
in `rocm_devel`; the script header explains why a symlink is the only correct
mechanism.

If a future nightly puts the bindings on `sys.path` itself, a build log prints
a `setup_amdsmi: RETIRE THIS SHIM` banner and the script makes no changes —
delete it and its `COPY`/`RUN` at that point. Watch for the banner when bumping `ROCM_RELEASE_ID`:

```sh
docker build -f docker/Dockerfile.ci --target rocm_devel . --progress=plain 2>&1 \
    | grep -i 'setup_amdsmi'
```

### LLVM classic toolkit path

`docker/setup_rocm_llvm_layout.sh` links `${ROCM_PATH}/lib/llvm/bin` tools into
`${ROCM_PATH}/llvm/bin`. Nightly debs install `ld.lld` under `lib/llvm/bin` and
leave `llvm/bin` as clang `.cfg` stubs; FlyDSL/MLIR still invoke
`$ROCM_PATH/llvm/bin/ld.lld`. It runs in `rocm_devel` after the ROCm `ENV`
block, which also puts `lib/llvm/bin` on `PATH`.

If a future nightly ships an executable `ld.lld` at the classic path, a build
log prints a `setup_rocm_llvm_layout: RETIRE THIS SHIM` banner and the script
makes no changes — delete it and its `COPY`/`RUN` at that point. Keep the
`PATH=/opt/rocm/lib/llvm/bin` `ENV` unless the nightly also puts that directory
on `PATH`. The shim can also be deleted if FlyDSL (or its bundled MLIR) stops
looking for `$ROCM_PATH/llvm/bin/ld.lld` and uses `lib/llvm/bin`,
`HIP_CLANG_PATH`, `TRITON_HIP_LLD_PATH`, or `PATH` instead; that will not
print the banner. Watch for the banner when bumping `ROCM_RELEASE_ID`:

```sh
docker build -f docker/Dockerfile.ci --target rocm_devel . --progress=plain 2>&1 \
    | grep -i 'setup_rocm_llvm_layout'
```

### Origami CMake package

`docker/setup_origami_cmake.sh` writes `${ROCM_HOME}/lib/cmake/origami/origami-config.cmake`
so `find_package(hipblaslt)` can configure. hipBLASLt's installed package config
requires `find_package(origami)` in a sibling `lib/cmake/origami` directory
(`NO_DEFAULT_PATH`, so `CMAKE_PREFIX_PATH` does not help). Nightly debs ship
`liborigami.so` in the BLAS host package but omit that CMake package from
BLAS devel. It runs in `rocm_devel` after the `-dev` debs, which is when
hipBLASLt's CMake files land. The script header explains why a stub imported
target is the only correct mechanism.

If a future nightly ships `origami-config.cmake` (or `origamiConfig.cmake`) next
to hipBLASLt, a build log prints a `setup_origami_cmake: RETIRE THIS SHIM`
banner and the script makes no changes — delete it and its `COPY`/`RUN` at that
point. The shim can also be deleted if hipBLASLt stops `find_dependency(origami)`;
that will not print the banner. Watch for the banner when bumping `ROCM_RELEASE_ID`:

```sh
docker build -f docker/Dockerfile.ci --target rocm_devel . --progress=plain 2>&1 \
    | grep -i 'setup_origami_cmake'
```
