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

The image does not build ROCm from source and does not support
`rocm-libraries` or `rocm-systems` commit overrides. Changes that are not
available in the pinned nightly must first be published in a nightly snapshot.

To build and validate every stage locally without a GPU:

```sh
docker build -f docker/Dockerfile.ci --target final -t pytorch-xdit-dev .
```