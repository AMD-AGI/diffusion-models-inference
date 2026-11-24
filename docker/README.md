# Dockerfiles for xDiT diffusion models
- `Dockerfile.ci` docker file for AMD images
- `Dockerfile.cuda` docker file for CUDA images
- `Dockerfile.rdna4` docker file for RDNA4 AMD images
- `Dockerfile.sgld` SGL-D image based on `amdsiloai/pytorch-xdit`, with separate SGL kernel
  libraries for gfx942 and gfx950. [patches/sglang-rocm-arch-loader.patch](../patches/sglang-rocm-arch-loader.patch)
  preserves explicit build targets and makes SGLang select the matching library at runtime.
- `Dockerfile.sgld.cuda` SGL-D image for CUDA based on `lmsysorg/sglang`
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

Both workflows call a framework-aware reusable build. The
[resolve-framework](../.github/actions/resolve-framework/action.yml) action
maps `framework` to the Dockerfile, repository defaults, and cache channel for
every build workflow; add a new framework there. ROCm callers can override the
image repository where needed. Manual dispatch builds only
the selected framework (xDiT is the default), while pushes continue to build
xDiT only. xDiT alone builds the untuned image and runs MIOpen tuning. The ROCm
images use separate cache channels scoped to the branch (override with
`cache_scope`), with cache export enabled on pushes or when
`export_docker_cache` is set. CUDA builds produce `-amd64` and `-arm64` tags,
then assemble a multi-architecture manifest at the unsuffixed tag.

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

### Build, tune, and benchmark a framework

[build-and-benchmark.yml](../.github/workflows/build-and-benchmark.yml) also
accepts `framework: xdit` (default), `sgld`, or `vllmo`, both through manual
dispatch and `workflow_call`. New builds select the same Dockerfiles,
repositories, and cache channels as the core-image workflow. SGL-D and
vLLM-Omni build a single image; benchmarks use its unsuffixed tag rather than
an xDiT staging image with `-temp`.

All three frameworks support optional MIOpen tuning, benchmarking, and MIOpen
DB branch creation. Tuning still uses this repository's shared workloads.
Final tuned-image publishing (`build_final`) is xDiT-only and is skipped with
a notice for the other frameworks. `use_xdit_configs` is also xDiT-only.

With `rebuild=false`, `base_image` can select an existing framework image;
bare tags resolve against the selected framework's repository (xDiT uses
`STAGING_IMAGE`). An empty `base_image` forces a build. On SGL-D/vLLM-Omni
rebuilds, forwarding `base_image` into the framework Dockerfile is not
implemented; those builds retain their Dockerfile base defaults.

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

### ROCm and PyTorch wheels

`Dockerfile.ci` installs ROCm, PyTorch, torchvision, torchaudio, and Triton as
TheRock Python wheels from the
[nightly wheel index](https://nightly.repo.amd.com/rocm/whl-next/).
`ROCM_WHEEL_VERSION` selects one TheRock build and defaults to
`10.3.0a20261108`. torch pins `rocm[libraries]` and `triton` to that exact
version, so all ROCm packages come from the same build. `TORCH_VERSION`,
`TORCHVISION_VERSION`, and `TORCHAUDIO_VERSION` select the framework
releases; the install adds the `+rocm${ROCM_WHEEL_VERSION}` local version.
The scheduled nightly workflow selects the latest ROCm wheel version that has
a `TORCH_VERSION` build on this index.

`ROCM_GFX_TARGETS` selects the `device-<gfx>` extras of torch and
torchvision. These pull the architecture-specific kernel wheels
(`amd-torch-device-<gfx>`, `amd-torchvision-device-<gfx>`, and
`rocm-sdk-device-<gfx>`). The same targets go to `MORI_GPU_ARCHS`.

`docker/install_rocm_wheels.sh` installs the wheels from the AMD index only.
The index mirrors the pure-Python dependencies of torch, so no PyPI package
can replace a ROCm build. Then it runs `rocm-sdk init` in the same layer,
links `/opt/rocm` to the initialized `rocm-sdk-devel` tree, and writes
`/opt/rocm-wheel-constraints.txt`. `PIP_CONSTRAINT` applies that file to
every later `pip` run, so AITER, diffusers, xDiT, and other dependencies
cannot replace torch, Triton, or the ROCm wheels.

| Target | Contents | Use |
| --- | --- | --- |
| `rocm_sdk` | ROCm SDK (`rocm-sdk-core`, `-libraries`, `-devel`, `rocm-profiler`, and device wheels for each arch in `ROCM_GFX_TARGETS`), torch, torchvision, torchaudio, Triton. `/opt/rocm` links to the devel tree | Parent of `build_mori` and `core` |

`build_mori` builds the MORI wheel from `rocm_sdk` without UMBP, and `core`
installs it after AITER; MORI provides the symmetric memory behind AITER's
intranode attention A2A.
`final` installs rocprofiler-compute's Python requirements.

```sh
# pinned ROCm wheel build
docker build -f docker/Dockerfile.ci --target final -t pytorch-xdit-dev .

# override the ROCm wheel build
docker build -f docker/Dockerfile.ci --target final \
    --build-arg ROCM_WHEEL_VERSION=10.3.0a20261124 \
    -t pytorch-xdit-nightly .
```

A `ROCM_WHEEL_VERSION` override needs a `TORCH_VERSION` build for the same
ROCm wheel version on the index. The image does not build ROCm or PyTorch
from source. Changes that are not available in a published wheel build must
first be published on the index.

### amd-smi Python bindings

`docker/setup_amdsmi.sh` links the ROCm Python bindings into the venv, because
the ROCm SDK wheels ship them under `${ROCM_HOME}/share/amd_smi/amdsmi` with no
`setup.py`, where pip cannot install them and nothing can import them. It runs
in `rocm_sdk`; the script header explains why a symlink is the only correct
mechanism.

If a future ROCm build puts the bindings on `sys.path` itself, a build log prints
a `setup_amdsmi: RETIRE THIS SHIM` banner and the script makes no changes —
delete it and its `COPY`/`RUN` at that point. Watch for the banner when bumping `ROCM_WHEEL_VERSION`:

```sh
docker build -f docker/Dockerfile.ci --target rocm_sdk . --progress=plain 2>&1 \
    | grep -i 'setup_amdsmi'
```
