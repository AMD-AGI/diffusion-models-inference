# PR Categorization Rules

Categorize each PR based on its changed file paths. When a PR touches files in
multiple categories, use the **primary purpose** of the PR to decide.

## Categories included in release notes

These changes affect the final Docker image (`amdsiloai/pytorch-xdit`).

### New Model / Model Update
File patterns:
- `benchmark_configs/xdit/*.yaml` — new config = new model included in the ROCm image
- Compare xfuser (xDiT) commits between releases to capture upstream model additions

Note: `src/` directories are legacy and do NOT indicate new models.

### Performance
File patterns:
- Changes to performance-related dependency commits in `docker/Dockerfile.ci`
  (e.g., AITER_COMMIT, XDIT_COMMIT)

Note: `src/` directories (attention_ops, spargeattn, etc.) are legacy/experimental — exclude.

### Docker / Environment
File patterns:
- `docker/Dockerfile.ci` — the main CI Dockerfile (this IS the image)
- Changes to dependency commit SHAs in Dockerfile.ci (AITER_COMMIT, XDIT_COMMIT, etc.)

This skill is intentionally scoped to the ROCm image built by `docker/Dockerfile.ci`.
Changes that only affect other image variants are outside these release notes.

### Benchmark Configurations
File patterns:
- `benchmark_configs/xdit/*.yaml` — benchmark configurations copied into the ROCm image
- New configs = new model benchmarks being tracked

### Libraries / Dependencies
File patterns:
- Changes to version pins in `docker/Dockerfile.ci` (ARG lines for commits)
- Patch files under `patches/` that are referenced by `docker/Dockerfile.ci`

Note: unrelated source tooling is excluded unless it changes the image build.
Benchmark configurations under `benchmark_configs/sgld/` and
`benchmark_configs/omni/` are not copied into this image and are out of scope.

## Categories excluded from release notes

These changes do NOT affect the final Docker image.

### CI / Workflows
File patterns:
- `.github/workflows/` — GitHub Actions workflow files
- `.github/actions/` — reusable actions
- `.github/skills/` — agent skills
- `.github/prompts/` — agent prompts
- `.github/instructions/` — agent instructions
- `Makefile` — build automation (unless it affects Docker builds)

### Documentation
File patterns:
- `docs/` — documentation
- `README.md` (root or any subdirectory)
- `*.md` files that are documentation only

### Scripts / Tooling
File patterns:
- `src/` — tooling and experiments (unless directly copied into the image build)
- `scripts/` — helper scripts
- `src/sweep_tooling/` — sweep tools

### Bug Fix (CI/tooling)
PRs whose title contains "fix" or "bug" AND only touch excluded file paths.
If a bug fix touches image-affecting files, it should be included under the
appropriate image-affecting category instead.

### MAD / Integrations
Always excluded. These are integration test configurations, not part of the image.
File patterns:
- `integrations/MAD/` — MAD integration files
- `integrations/` — any integration-related files

### MIOpen / Tuning
Always excluded unless the PR also changes the Dockerfile (in which case, categorize
under Docker / Environment instead).
File patterns:
- `data/miopen/` — MIOpen user databases
- `src/miopen_convolution/` — MIOpen convolution code
- `src/miopen-install/` — MIOpen installation scripts

## Edge Cases

### PR touches both image and non-image files
Include it. Categorize by the image-affecting change.

### Dependency version bump in Dockerfile.ci
Categorize as **Libraries / Dependencies** if it's a library update (e.g., xDiT,
diffusers, aiter). Categorize as **Docker / Environment** if it's a base image
or system-level change (e.g., ROCm, PyTorch, Triton).

### New benchmark config without model code changes
Categorize as **Benchmark Configurations**. This means a new model is being
benchmarked but no code was added — the model support may have come from an
upstream library update.

### Revert PRs
Include them if the original PR was included. Note it as a revert.
