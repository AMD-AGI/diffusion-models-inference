# Dockerfiles for xDiT diffusion models
- `Dockerfile.ci` docker file for AMD images
- `Dockerfile.cuda` docker file for CUDA images

## How-tos
- To build a custom `rocm-libraries` commit (e.g., to try out a GEMM tuning PR or other feature not merged to main and not available via TheRock), see [this guide](./../docs/TheRock/custom-rocm-libraries.md)