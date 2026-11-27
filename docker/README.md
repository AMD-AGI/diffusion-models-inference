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
`ROCM_WHEEL_VERSION` selects one TheRock build (see its `ARG` default in
`Dockerfile.ci`). torch pins `rocm[libraries]` and `triton` to that exact
version, so all ROCm packages come from the same build. `TORCH_VERSION`,
`TORCHVISION_VERSION`, and `TORCHAUDIO_VERSION` select the framework
releases; the install adds the `+rocm${ROCM_WHEEL_VERSION}` local version.
`ROCM_WHEEL_PYTHON_TAG` must agree with `python3` of `BASE_IMAGE`; the
install stops if it does not.

The index keeps nightly builds for about three months. After that, an image
with an older `ROCM_WHEEL_VERSION` cannot be built again. The pushed image is
the reproducible artifact.

The scheduled nightly workflow builds the newest ROCm wheel build on this
index. The
[resolve-rocm-nightly](../.github/actions/resolve-rocm-nightly/action.yml)
action gets the requirement list from
`install_rocm_wheels.sh --print-requirements` and checks the wheel file
names. If the newest build does not have a pinned wheel, the workflow fails
with the missing wheels; then update the pins in `Dockerfile.ci`. Notices
tell about newer torch, torchvision, and torchaudio releases and a new ROCm
version. The job summary and the `rocm-nightly-provenance` artifact record
the wheels. If the staging image `nightly-<ROCM_WHEEL_VERSION>` exists, the
ROCm build did not change, and the workflow does not build. A manual run with
`force_build` builds it again.

`ROCM_GFX_TARGETS` selects the `device-<gfx>` extras of torch and
torchvision. These pull the architecture-specific kernel wheels
(`amd-torch-device-<gfx>`, `amd-torchvision-device-<gfx>`, and
`rocm-sdk-device-<gfx>`). The same targets go to `MORI_GPU_ARCHS`.

`docker/install_rocm_wheels.sh` installs the wheels from the AMD index only.
The index mirrors the pure-Python dependencies of torch, so no PyPI package
can replace a ROCm build. Then it runs `rocm-sdk init` in the same layer,
links `/opt/rocm` to the initialized `rocm-sdk-devel` tree, and writes
the pinned versions to the pip constraints file
`/opt/rocm-wheel-constraints.txt`. It also writes the build identity to
`/opt/rocm-wheel-build-info.txt` and a copy of the TheRock manifest to
`/opt/rocm-wheel-therock-manifest.json` (see
[ROCm and PyTorch provenance](#rocm-and-pytorch-provenance)). The image
environment variables `ROCM_WHEEL_CONSTRAINTS`, `ROCM_WHEEL_BUILD_INFO`, and
`ROCM_WHEEL_THEROCK_MANIFEST` give these paths, and the record-image-versions
action copies the files into the versions artifact.

During the build, a build argument (`ARG PIP_CONSTRAINT` in `core`) applies
the constraints file to the `pip` runs in `core` and `final`. Thus AITER,
diffusers, xDiT, and other dependencies cannot replace torch, Triton, or the
ROCm wheels. `install_rocm_wheels --check` after the package installs in
`final` makes sure of this. The published image does not set
`PIP_CONSTRAINT`. To keep the ROCm
wheels in a downstream image or a container, use the file explicitly:

```sh
pip install -c "${ROCM_WHEEL_CONSTRAINTS}" <packages>
```

or, in a Dockerfile, for all later `pip` runs:

```dockerfile
ENV PIP_CONSTRAINT="${ROCM_WHEEL_CONSTRAINTS}"
```

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

A `ROCM_WHEEL_VERSION` override needs builds of all pinned wheels for the
same ROCm wheel version on the index. To see the list, run
`ROCM_WHEEL_VERSION=... ROCM_GFX_TARGETS=... TORCH_VERSION=...
TORCHVISION_VERSION=... TORCHAUDIO_VERSION=...
docker/install_rocm_wheels.sh --print-requirements`.
The image does not build ROCm or PyTorch
from source. Changes that are not available in a published wheel build must
first be published on the index.

### ROCm and PyTorch provenance

`ROCM_WHEEL_VERSION` (for example `10.3.0a20261108`) identifies one TheRock
nightly build. That build sets the source of all ROCm and PyTorch packages in
the image. The torch, torchvision, and torchaudio wheel versions are
`<*_VERSION>+rocm<ROCM_WHEEL_VERSION>`, for example
`torch 2.14.0+rocm10.3.0a20261108`. TheRock builds these wheels and Triton
again for each ROCm wheel build, from the ROCm/pytorch release branch and the
torchvision, torchaudio, and Triton commits that it pins (`related_commits`
and `.ci/docker/ci_commit_pins/triton.txt`). Thus a new `ROCM_WHEEL_VERSION`
can contain different commits even when the `*_VERSION` pins do not change.

The build info file (`$ROCM_WHEEL_BUILD_INFO`) gives each source commit as a
`key<TAB>value` line:

| Component | Build info key | Repository |
| --- | --- | --- |
| TheRock | `therock_manifest.the_rock_commit`; `therock_manifest.github_run_id` is the TheRock CI run | [ROCm/TheRock](https://github.com/ROCm/TheRock) |
| ROCm components (rocm-systems, rocm-libraries, llvm-project, and the other TheRock submodules) | `therock_manifest.submodule.<path>`; the value is `<commit> <repository>` | The repository in the value |
| torch | `torch.version.git_version` | [ROCm/pytorch](https://github.com/ROCm/pytorch) |
| torchvision | `torchvision.version.git_version` | [ROCm/vision](https://github.com/ROCm/vision) |
| torchaudio | `torchaudio.version.git_version` | [ROCm/audio](https://github.com/ROCm/audio) |
| Triton | `triton.git_version` (short commit) | [triton-lang/triton](https://github.com/triton-lang/triton) |

The TheRock manifest (`$ROCM_WHEEL_THEROCK_MANIFEST`) is the full record of
the TheRock build. It also lists the TheRock patches of each submodule and
the build flags.

**From an image:**

```sh
docker run --rm IMAGE sh -c 'cat "$ROCM_WHEEL_BUILD_INFO"'
docker run --rm IMAGE sh -c 'cat "$ROCM_WHEEL_THEROCK_MANIFEST"'
```

CI runs that build or benchmark an image upload the same data in the
`<run_number>-versions-<arch>` artifact: the build info in `rocm.txt`
(section "ROCm wheel build info") and the manifest in
`therock-manifest.json`.

**From the wheel index, without an image:** the index keeps a build for about
three months. The `rocm-sdk-core` wheel contains the TheRock manifest, and the
torch, torchvision, and torchaudio wheels contain their source commits. The
torch wheel metadata gives the exact Triton build. The download is about
700 MB.

```sh
version=10.3.0a20261108                    # ROCM_WHEEL_VERSION
torch=2.14.0 vision=0.29.0 audio=2.11.0.3  # *_VERSION pins of the same Dockerfile.ci
wheels="$(mktemp -d)"
pip download --no-deps --only-binary=:all: \
    --python-version 3.12 --platform linux_x86_64 \
    --index-url https://nightly.repo.amd.com/rocm/whl-next -d "$wheels" \
    "rocm-sdk-core==${version}" "torch==${torch}+rocm${version}" \
    "torchvision==${vision}+rocm${version}" "torchaudio==${audio}+rocm${version}"
unzip -p "$wheels"/rocm_sdk_core-*.whl \
    _rocm_sdk_core/share/therock/therock_manifest.json
echo
for package in torch torchvision torchaudio; do
    unzip -p "$wheels/${package}"-*.whl "${package}/version.py" | grep '^git_version'
done
unzip -p "$wheels"/torch-*.whl 'torch-*.dist-info/METADATA' |
    grep '^Requires-Dist: triton'
```

`--python-version` must agree with `ROCM_WHEEL_PYTHON_TAG` (`cp312` is
3.12).

### amd-smi Python bindings

`docker/setup_amdsmi.sh` links the amd-smi Python bindings into the venv. The
`rocm-sdk-core` wheel installs them in its private tree
(`_rocm_sdk_core/share/amd_smi/amdsmi`), and `rocm-sdk init` mirrors them into
the `rocm-sdk-devel` tree that `/opt/rocm` links to. Neither location is on
`sys.path`, so without the link `import amdsmi` fails. The `amd-smi` command
needs no shim: it is in both `/opt/rocm/bin` and `/venv/bin`.

The script runs in `rocm_sdk` and links `site-packages/amdsmi` to
`/opt/rocm/share/amd_smi/amdsmi`. The wrapper finds
`libamd_smi.so.<SOVERSION>` relative to the resolved path of its own file, so
the link loads the library from the same ROCm tree. The script header explains
why a copy or a `sys.path` entry is wrong.

If a future ROCm build puts the bindings on `sys.path` itself, a build log prints
a `setup_amdsmi: RETIRE THIS SHIM` banner and the script makes no changes —
delete it and its `COPY`/`RUN` at that point. Watch for the banner when bumping `ROCM_WHEEL_VERSION`:

```sh
docker build -f docker/Dockerfile.ci --target rocm_sdk . --progress=plain 2>&1 \
    | grep -i 'setup_amdsmi'
```

If `import amdsmi` resolves outside `/opt/rocm`, the banner says the bindings
are probably from a PyPI `amdsmi` wheel. A future `rocm-sdk-core` that installs
`amdsmi` directly into `site-packages` also triggers this note. To tell the two
apart, look for `libamd_smi_python.so` next to the resolved `amdsmi` package.
Only the PyPI wheel ships that file.
