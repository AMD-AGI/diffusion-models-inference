# Diffusion model inference

This repository contains benchmark configurations, profiling tools, tuning data, and
container build infrastructure for optimized diffusion model inference. The xDiT workloads
cover image and video generation on AMD GPU architectures.

## Container images

Release images are distributed through the
[rocm/pytorch-xdit](https://hub.docker.com/r/rocm/pytorch-xdit) repository on Docker Hub.
The image packages the inference environment and the optimizations validated by this
repository. Available tags and pull instructions are listed on the Docker Hub page.

## Supported models

The current xDiT benchmark configurations include:

| Model family | Configured model |
|---|---|
| FLUX.1 | `black-forest-labs/FLUX.1-dev` |
| FLUX.1 Kontext | `black-forest-labs/FLUX.1-Kontext-dev` |
| FLUX.2 | `black-forest-labs/FLUX.2-dev` |
| FLUX.2 Klein | `black-forest-labs/FLUX.2-klein-9B` |
| HunyuanVideo | `tencent/HunyuanVideo` |
| HunyuanVideo 1.5 | Text-to-video, distilled image-to-video, and sparse variants |
| LTX-2.3 | `dg845/LTX-2.3-Diffusers` |
| Qwen-Image | `Qwen/Qwen-Image-2512` |
| Qwen-Image-Edit | `Qwen/Qwen-Image-Edit` |
| Stable Diffusion 3.5 | `stabilityai/stable-diffusion-3.5-large` |
| Wan 2.1 | `Wan-AI/Wan2.1-I2V-14B-720P-Diffusers` |
| Wan 2.2 | `Wan-AI/Wan2.2-I2V-A14B-Diffusers` |
| Z-Image | `Tongyi-MAI/Z-Image` |
| Z-Image Turbo | `Tongyi-MAI/Z-Image-Turbo` |

See [`benchmark_configs/xdit/`](benchmark_configs/xdit/) for the complete workload variants,
model identifiers, parallelism settings, and model-specific options.

## Supported GPU architectures

The xDiT configurations currently define workloads for these GPU targets:

| Platform | Architecture tags |
|---|---|
| AMD Instinct | `gfx942`, `gfx950` |
| AMD RDNA 4 | `gfx1201` |

Support varies by model and workload. Consult the `tags` in the relevant
[`benchmark_configs/xdit/`](benchmark_configs/xdit/) YAML file before selecting a target.

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development
workflow, testing expectations, code ownership, licensing guidance, and local pre-commit
checks.

## Reporting vulnerabilities

Do not report security vulnerabilities through public GitHub issues. Follow the private
reporting options and include the requested diagnostic information described in
[SECURITY.md](SECURITY.md).
