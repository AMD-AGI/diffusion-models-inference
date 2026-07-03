# Dockerfiles for xDiT diffusion models
- `Dockerfile.ci` docker file for AMD images
- `Dockerfile.cuda` docker file for CUDA images
- `Dockerfile.rdna4` docker file for RDNA4 AMD images
- `Dockerfile.sgld` SGL-D image based on `amdsiloai/pytorch-xdit`
- `Dockerfile.sgld_lmsys` SGL-D image based on `lmsysorg/sglang-rocm` with CI scripts from xDiT
- `Dockerfile.vllm_omni` vLLM-Omni image for ROCm based on `amdsiloai/pytorch-xdit`
- `Dockerfile.vllm_omni.cuda` vLLM-Omni image for CUDA based on `vllm/vllm-openai`

## How-tos
- To build a custom `rocm-libraries` or `rocm-systems` commit (e.g., to try out a GEMM tuning PR or other feature not merged to main and not available via TheRock), see [this guide](./../docs/TheRock/custom-rocm-libraries.md)