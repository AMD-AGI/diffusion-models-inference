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

`Dockerfile.ci` installs ROCm, PyTorch, torchvision, torchaudio, and Triton
from TheRock's unified [multi-arch nightly wheel
index](https://nightly.repo.amd.com/rocm/whl-next/). The version arguments must
describe one coherent published stack:

| Build argument | Purpose |
| --- | --- |
| `THEROCK_WHEEL_INDEX` | Aggregate ROCm/framework Python index |
| `ROCM_WHEEL_VERSION` | `rocm` SDK version required by PyTorch |
| `PYTORCH_WHEEL_VERSION` | PyTorch version; also pins its matching Triton |
| `PYTORCH_VISION_WHEEL_VERSION` | Matching torchvision version |
| `PYTORCH_AUDIO_WHEEL_VERSION` | Matching torchaudio version; empty omits it |
| `ROCM_GFX_TARGETS` | `;`-separated device extras and MORI build architectures, such as `gfx942;gfx950` |

PyTorch, torchvision, torchaudio, and Triton source commits are selected by
TheRock when it publishes the pinned wheels; they are not independently
configurable by this Dockerfile.

The SDK is installed in `/venv` and expanded by `rocm-sdk init`.
`ROCM_HOME`, compiler/CMake paths, and a compatibility `/opt/rocm` symlink
point at that expanded tree. `rocm_devel` is the parent of `build_mori` and
`core`; it contains the wheel stack, SDK headers/toolchain, and profiler.
MORI and AITER remain source builds. Torchcodec is omitted because TheRock does
not publish it. The locally built non-GPL FFmpeg remains for video export.

```sh
# Pinned defaults
docker build -f docker/Dockerfile.ci --target final -t pytorch-xdit-dev .

# Override with another coherent wheel stack
docker build -f docker/Dockerfile.ci --target final \
    --build-arg ROCM_WHEEL_VERSION=10.3.0a20261104 \
    --build-arg PYTORCH_WHEEL_VERSION=2.16.0a0+rocm10.3.0a20261104 \
    --build-arg PYTORCH_VISION_WHEEL_VERSION=0.30.0a0+rocm10.3.0a20261104 \
    --build-arg PYTORCH_AUDIO_WHEEL_VERSION=2.11.1+rocm10.3.0a20261104 \
    -t pytorch-xdit-wheels .
```

The scheduled nightly workflow selects the highest published PyTorch release
line, then its newest complete Python 3.12 Linux stack, and passes all four
version arguments. ROCm can publish ahead of PyTorch; the workflow therefore
follows PyTorch's exact ROCm dependency instead of independently choosing the
newest `rocm` package.

### amd-smi Python bindings

`docker/setup_amdsmi.sh` links the SDK's matching Python bindings from
`${ROCM_HOME}/share/amd_smi/amdsmi` into the venv when wheel packaging does not
make them importable directly. It runs after `rocm-sdk init`; the script header
explains why a symlink is used instead of installing an unrelated PyPI wheel.

If a future nightly puts the bindings on `sys.path` itself, a build log prints
a `setup_amdsmi: RETIRE THIS SHIM` banner and the script makes no changes —
delete it and its `COPY`/`RUN` at that point. Watch for the banner when bumping
`ROCM_WHEEL_VERSION`:

```sh
docker build -f docker/Dockerfile.ci --target rocm_devel . --progress=plain 2>&1 \
    | grep -i 'setup_amdsmi'
```

The wheel development tree already supplies both the classic
`${ROCM_PATH}/llvm/bin/ld.lld` path required by FlyDSL and the Origami CMake
package required by hipBLASLt. The former DEB-layout shims for those paths have
therefore been removed.
