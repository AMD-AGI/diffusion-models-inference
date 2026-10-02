# Dockerfiles for xDiT diffusion models
- `Dockerfile.ci` docker file for AMD images
- `Dockerfile.cuda` docker file for CUDA images
- `Dockerfile.rdna4` docker file for RDNA4 AMD images
- `Dockerfile.sgld` SGL-D image based on `amdsiloai/pytorch-xdit`
- `Dockerfile.sgld_lmsys` SGL-D image based on `lmsysorg/sglang-rocm` with CI scripts from xDiT
- `Dockerfile.vllm_omni` vLLM-Omni image for ROCm based on `amdsiloai/pytorch-xdit`
- `Dockerfile.vllm_omni.cuda` vLLM-Omni image for CUDA based on `vllm/vllm-openai`

## How-tos

### ROCm nightly packages

`Dockerfile.ci` installs ROCm from a pinned TheRock nightly deb snapshot on the
[legacy multi-arch native index](https://rocm.nightlies.amd.com/packages-multi-arch/deb/).
Update `ROCM_RELEASE_ID` and `ROCM_DEB_SERIES` together when moving to a new
snapshot or ROCm release series. `ROCM_GFX_TARGETS` controls which architecture-specific
package shards are installed and is independent of `PYTORCH_ROCM_ARCH`, which
controls the architectures built into PyTorch and related wheels.

Leaf packages install under `/opt/rocm/core-<series>`. `rocm_tree` and `rocm_base`
both register the unversioned `/opt/rocm/{bin,lib,include,...}` links that the
`amdrocm-core` postinst would have created. They are separate installs from
`base`: `rocm_base` drops LLVM static archives outside `lib/clang` in that same
install step, and `rocm_tree` keeps them so `rocm_devel` can inherit the files.

| Target | Packages | Use |
| --- | --- | --- |
| `rocm_tree` | HIP/`hipcc`, BLAS, MIOpen, RCCL, FFT, RAND, sparse, and solver for each arch in `ROCM_GFX_TARGETS`, plus headers. LLVM static archives kept | Parent of `build_torch_stack` and `rocm_devel` |
| `rocm_base` | Same packages. LLVM static archives outside `lib/clang` removed | Lean runtime target |
| `rocm_devel` | `rocm_tree` plus developer-tools, RDC, OpenCL, blas/rccl tests | Default parent of `core` / `deps` / `final` |

`amdrocm-developer-tools` pulls the profiler, emulator, and debugger. It does
not depend on `amdrocm-core`. `core` is `FROM ${ROCM_PARENT}`, which defaults to
`rocm_devel`. Both parents install the wheels built by `build_torch_stack`.
`deps` installs rocprofiler-compute's Python requirements only when that tree
is present.

```sh
# developer tools included
docker build -f docker/Dockerfile.ci --target final -t pytorch-xdit-dev .

# runtime + torch stack + AITER JIT, without developer tools
docker build -f docker/Dockerfile.ci --target final \
    --build-arg ROCM_PARENT=rocm_base -t pytorch-xdit-lean .
```

The image does not build ROCm from source and does not support
`rocm-libraries` or `rocm-systems` commit overrides. Changes that are not
available in the pinned nightly must first be published in a nightly snapshot.

### amd-smi Python bindings

`docker/setup_amdsmi.sh` links the ROCm Python bindings into the venv, because
the nightly debs ship them under `${ROCM_HOME}/share/amd_smi/amdsmi` with no
`setup.py`, where pip cannot install them and nothing can import them. It runs
in `rocm_tree` and `rocm_base`; the script header explains why a symlink is the only correct
mechanism.

If a future nightly puts the bindings on `sys.path` itself, a build log prints
a `setup_amdsmi: RETIRE THIS SHIM` banner and the script makes no changes —
delete it and its `COPY`/`RUN` at that point. Watch for the banner when bumping `ROCM_RELEASE_ID`:

```sh
docker build -f docker/Dockerfile.ci --target rocm_base . --progress=plain 2>&1 \
    | grep -i 'setup_amdsmi'
```

### LLVM classic toolkit path

`docker/setup_rocm_llvm_layout.sh` links `${ROCM_PATH}/lib/llvm/bin` tools into
`${ROCM_PATH}/llvm/bin`. Nightly debs install `ld.lld` under `lib/llvm/bin` and
leave `llvm/bin` as clang `.cfg` stubs; FlyDSL/MLIR still invoke
`$ROCM_PATH/llvm/bin/ld.lld`. It runs in `rocm_tree` and `rocm_base` after the ROCm `ENV`
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
docker build -f docker/Dockerfile.ci --target rocm_base . --progress=plain 2>&1 \
    | grep -i 'setup_rocm_llvm_layout'
```

### Origami CMake package

`docker/setup_origami_cmake.sh` writes `${ROCM_HOME}/lib/cmake/origami/origami-config.cmake`
so `find_package(hipblaslt)` can configure. hipBLASLt's installed package config
requires `find_package(origami)` in a sibling `lib/cmake/origami` directory
(`NO_DEFAULT_PATH`, so `CMAKE_PREFIX_PATH` does not help). Nightly debs ship
`liborigami.so` in the BLAS host package but omit that CMake package from
BLAS devel. It runs in `rocm_tree` and `rocm_base` after the `-dev` debs, which is when
hipBLASLt's CMake files land. The script header explains why a stub imported
target is the only correct mechanism.

If a future nightly ships `origami-config.cmake` (or `origamiConfig.cmake`) next
to hipBLASLt, a build log prints a `setup_origami_cmake: RETIRE THIS SHIM`
banner and the script makes no changes — delete it and its `COPY`/`RUN` at that
point. The shim can also be deleted if hipBLASLt stops `find_dependency(origami)`;
that will not print the banner. Watch for the banner when bumping `ROCM_RELEASE_ID`:

```sh
docker build -f docker/Dockerfile.ci --target rocm_base . --progress=plain 2>&1 \
    | grep -i 'setup_origami_cmake'
```
