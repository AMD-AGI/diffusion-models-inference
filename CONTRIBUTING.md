# Contributing to diffusion-models-inference

This repository contains scripts, configurations, and tooling for benchmarking, profiling, and
optimizing diffusion model inference on AMD GPUs. It is the source for the public
[rocm/pytorch-xdit](https://hub.docker.com/r/rocm/pytorch-xdit) Docker image.

We welcome contributions from the community. Please review the following guidance before
submitting issues or pull requests. Security vulnerabilities must be reported privately
according to the [security policy](SECURITY.md), not through a public issue.

## Repository structure

| Directory | Purpose |
|---|---|
| `benchmark_configs/` | YAML benchmark configurations organized by runner (`xdit`, `sgld`, `omni`) |
| `docker/` | Dockerfiles for different targets (ROCm, CUDA, RDNA4, SGL-D, vLLM-Omni) |
| `src/` | Python tools — `distrituner`, `miopen_convolution`, `sweep_tooling` |
| `data/` | MIOpen and hipBLASLt tuning databases and workload definitions |
| `patches/` | Patches applied to upstream projects (xFuser, Dynamo, etc.) |
| `.ci/` | CI benchmark runner scripts and quality checks |
| `integrations/` | External integration tooling (e.g. MAD) |
| `assets/` | Documentation and reference data |

## Development workflow

### Issue tracking

Before filing a new issue, search the
[existing issues](https://github.com/AMD-AGI/diffusion-models-inference/issues) to check
whether your issue is already tracked.

When creating a new issue, use one of the provided templates:

* **Release** — for tracking image releases
* **Feature** — for feature requests
* **Bug** — for bug reports

Provide as much context as possible, including hardware, software versions, and steps to reproduce.

### Pull requests

All pull requests should target the **main** branch.

The repository includes a
[pull request template](.github/pull_request_template.md) that asks for background, goals,
tasks, and test results. Please fill it out thoroughly.

When creating a PR:

* Link to the relevant issue
* Ensure your code builds successfully (at minimum, verify a local Docker build)
* Run existing unit tests and add new tests for new functionality
* Do not break existing tests
* Describe what you tested and on what hardware in the PR
* Report benchmark numbers when applicable

Once submitted, a reviewer from the appropriate team will be assigned based on the
[CODEOWNERS](.github/CODEOWNERS) file. After approval, the change enters the internal CI
pipeline for validation on supported GPU architectures before it can be included in a release.

> [!IMPORTANT]
> By creating a PR, you agree to allow your contribution to be licensed under the terms of the
> [LICENSE](LICENSE) file in this repository.

## Code ownership

Reviewers are automatically assigned via [CODEOWNERS](.github/CODEOWNERS). The ownership
rules are:

| Area | Scope |
|---|---|
| Default | All files — `@AMD-AGI/siloai-diffusion-inference` |
| Core | `benchmark_common/`, `benchmark_configs/`, `data/`, `integrations/`, `src/` — `@AMD-AGI/siloai-diffusion-inference-core` |
| CI | `.github/workflows/`, `.github/actions/`, `docker/`, `Makefile` — `@AMD-AGI/siloai-diffusion-inference-ci` |
| Admin | `.github/CODEOWNERS`, `.github/pull_request_template.md`, `CONTRIBUTING.md`, `SECURITY.md`, `LICENSE` — `@AMD-AGI/siloai-diffusion-inference-admin` |

## Testing and quality

### What contributors should do

* **Unit tests** — run tests under `src/*/tests/` for any Python tool changes
* **Local Docker build** — verify that your changes don't break the image build
  (see `docker/` for the relevant Dockerfile)
* **Local inference** — if you have access to AMD GPU hardware, run the affected
  workloads and verify functionality and output quality
* **Document your testing** — clearly describe in the PR what you tested, on what
  hardware, and include benchmark numbers or sample outputs where relevant

### What happens after merge

Contributors do not have access to the full CI and release pipelines. After a PR is
merged, the maintainer team runs the internal build-and-benchmark pipeline across
supported GPU architectures to validate performance and output quality. Changes that
affect the public `rocm/pytorch-xdit` image go through additional quality gates before
release.

If the internal validation reveals regressions, the team will follow up with you.

## Docker

Development and testing are container-based. The `docker/` directory contains Dockerfiles
for various targets — see [docker/README.md](docker/README.md) for descriptions.

To build an image locally:

```sh
docker build -f docker/Dockerfile.ci -t pytorch-xdit-dev .
```

The CI pipeline (`.github/workflows/build-and-benchmark.yml`) automates image builds
and benchmark runs on supported hardware. The release path from staging to the public
`rocm/pytorch-xdit` image is managed internally.

## Common contribution patterns

### Adding a new model or workload

1. Add a benchmark YAML config to `benchmark_configs/<runner>/`
2. Add MIOpen workload definitions to `data/miopen/workloads/` if applicable
3. Add reference outputs for quality validation
4. Update CI scripts in `.ci/` if the new workload needs to be included in automated runs

### Adding or updating tuning data

Tuning databases live under `data/miopen/` and `data/hipblaslt/`. Include the workload
files and database entries for the relevant GPU architectures.

### Upstream patches

Patches for upstream projects go in `patches/`. Name the patch file descriptively and
ensure it applies cleanly against the upstream version pinned in the Dockerfiles. These
patches apply to upstream code governed by the upstream project's license. Preserve
license headers from upstream; patch files are excluded from first-party SPDX checks.

### Pre-commit hooks

Install [pre-commit](https://pre-commit.com/) and run `pre-commit install` to enable the
repository checks locally. First-party data files covered by the check must start with:

```text
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
```

The hook currently verifies these headers on the applicable first-party data files.
Patch files are excluded because they may contain legitimate upstream license headers.
The checks will be expanded as the repository is prepared for public development.