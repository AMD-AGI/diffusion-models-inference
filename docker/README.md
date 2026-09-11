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

`Dockerfile.ci` installs ROCm from a pinned TheRock nightly deb snapshot. Update
`ROCM_RELEASE_ID` and `ROCM_DEB_SERIES` together when moving to a new snapshot
or ROCm release series. `ROCM_GFX_TARGETS` controls which architecture-specific
package shards are installed and is independent of `PYTORCH_ROCM_ARCH`, which
controls the architectures built into PyTorch and related wheels.

ROCm is layered as three named stages (later layers add packages only):

| Target | Packages | Use |
| --- | --- | --- |
| `rocm_runtime` | `amdrocm-core${ROCM_DEB_SERIES}-${gfx}` | Runtime libs and `hipcc` |
| `rocm_runtime_jit` | plus `amdrocm-core-dev${ROCM_DEB_SERIES}-${gfx}` | Headers for AITER / Triton compile |
| `rocm_devel` | plus developer-tools, RDC, OpenCL, blas/rccl tests | Parent of `core` / `final` / `build_torch_stack` |

`--target core` and `--target final` still inherit `rocm_devel` (full tools and
test debs). `rocm_runtime` and `rocm_runtime_jit` are ancestor cache layers and a
placeholder for a leaner product image; they are not CI tags.

A later lean cutover is: point `core` at `rocm_runtime_jit`. That drops
developer-tools, RDC, OpenCL, and test debs from the shipped image, and the
`deps` rocprofiler-compute pip install must move or go away with that parent.
`build_torch_stack` can move to `rocm_runtime_jit` in the same change if torch
rebuilds should no longer follow test/tool deb churn.

The image does not build ROCm from source and does not support
`rocm-libraries` or `rocm-systems` commit overrides. Changes that are not
available in the pinned nightly must first be published in a nightly snapshot.

To build and validate every stage locally without a GPU:

```sh
docker build -f docker/Dockerfile.ci --target final -t pytorch-xdit-dev .
```

### amd-smi Python bindings

`docker/setup_amdsmi.sh` links the ROCm Python bindings into the venv, because
the nightly debs ship them under `${ROCM_HOME}/share/amd_smi/amdsmi` with no
`setup.py`, where pip cannot install them and nothing can import them. It runs
in `rocm_runtime`; the script header explains why a symlink is the only correct
mechanism.

If a future nightly puts the bindings on `sys.path` itself, a build log prints
a `setup_amdsmi: RETIRE THIS SHIM` banner and the script makes no changes —
delete it and its `COPY`/`RUN` at that point. Watch for the banner when bumping `ROCM_RELEASE_ID`:

```sh
docker build -f docker/Dockerfile.ci --target rocm_runtime . --progress=plain 2>&1 \
    | grep -i 'setup_amdsmi'
```