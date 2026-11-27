# Commands — create-release-notes

## Resolve context

```bash
REPO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner')
DEFAULT_BRANCH=$(gh repo view "$REPO" --json defaultBranchRef --jq '.defaultBranchRef.name')
```

## List recent tags

```bash
git tag --list 'v*' --sort=-version:refname | head -10
```

## List release commits

From first-parent commits between two refs:

```bash
git log FROM_TAG..TO_REF --first-parent --format='%H%x09%s'
```

## Resolve associated PRs

For each commit SHA, ask GitHub which public PR contains it:

```bash
gh api \
  -H 'Accept: application/vnd.github+json' \
  "repos/$REPO/commits/COMMIT_SHA/pulls" \
  --jq 'map({number, title, url: .html_url, author: .user.login})'
```

For a confirmed PR, fetch its changed paths:

```bash
gh pr view PR_NUMBER --repo "$REPO" \
  --json number,title,url,files,author \
  --jq '{number, title, url, author: .author.login, files: [.files[].path]}'
```

For commits without an associated public PR, retain changed paths locally:

```bash
git diff-tree --no-commit-id --name-only -r COMMIT_SHA
```

## Diff dependency pins

Show which dependency pins changed in `docker/Dockerfile.ci` between releases.
Include `*_COMMIT`, the `*_VERSION` wheel pins, and the ROCm wheel index:

```bash
git diff FROM_TAG..TO_REF -- docker/Dockerfile.ci | \
  grep -E '^[+-]ARG\s+(\w+_COMMIT|\w+_VERSION|ROCM_WHEEL_INDEX)=' | sort
```

## Look up upstream changes

For each changed commit hash (`OLD_HASH` → `NEW_HASH`), query the upstream repo
to see what was added. Use the repo mapping below to resolve the GitHub org/repo.
Record `ROCM_WHEEL_VERSION` changes as TheRock nightly wheel build updates
(`10.3.0aYYYYMMDD`), and `TORCH_VERSION`, `TORCHVISION_VERSION`, and
`TORCHAUDIO_VERSION` changes as PyTorch release updates. These are not Git
commit hashes; get their commits from
[ROCm wheel provenance](#rocm-wheel-provenance).

```bash
gh api repos/OWNER/REPO/compare/OLD_HASH...NEW_HASH \
  --jq '.commits[] | {sha: .sha[0:7], message: (.commit.message | split("\n")[0])}'
```

### Repo mapping

| ARG name | GitHub repo |
|---|---|
| AITER_COMMIT | ROCm/aiter |
| LONGCONTEXTATTENTION_COMMIT | feifeibear/long-context-attention |
| DIFFUSERS_COMMIT | huggingface/diffusers |
| DISTVAE_COMMIT | xdit-project/DistVAE |
| XDIT_COMMIT | xdit-project/xDiT |

### ROCm wheel provenance

`ROCM_WHEEL_VERSION` identifies one TheRock nightly build, and that build
sets the commits of ROCm, torch, torchvision, torchaudio, and Triton. Report
torch, torchvision, and torchaudio with the full wheel version: the
`*_VERSION` pin plus `+rocm<ROCM_WHEEL_VERSION>`, for example
`torch 2.14.0+rocm10.3.0a20261108`. TheRock builds these wheels again for each
ROCm wheel build, so a `ROCM_WHEEL_VERSION` change can change their source
even when the `*_VERSION` pins stay the same. Thus, when `ROCM_WHEEL_VERSION`
changes, get the commits of both releases and compare each commit that
differs. Details: `docker/README.md`, section "ROCm and PyTorch provenance".

Get the build info (`key<TAB>value` lines) of each release, with the first
source that is available:

1. The release image:
   ```bash
   docker run --rm IMAGE sh -c 'cat "$ROCM_WHEEL_BUILD_INFO"'
   docker run --rm IMAGE sh -c 'cat "$ROCM_WHEEL_THEROCK_MANIFEST"'  # full manifest, with patches
   ```
2. The `<run_number>-versions-<arch>` artifact of the CI run that built the
   release image: `rocm.txt`, section "ROCm wheel build info", and
   `therock-manifest.json`.
3. The wheel index, for about three months after the build. Use the
   `pip download` and `unzip -p` commands in `docker/README.md`. They read the
   manifest from the `rocm-sdk-core` wheel, `git_version` from
   `<package>/version.py` in the torch, torchvision, and torchaudio wheels, and
   the Triton pin from the torch wheel metadata.

| Build info key | GitHub repo |
|---|---|
| `therock_manifest.the_rock_commit` | ROCm/TheRock |
| `therock_manifest.submodule.<path>` (value: `<commit> <repository>`), for example `rocm-systems` and `rocm-libraries` | The repository in the value |
| `torch.version.git_version` | ROCm/pytorch |
| `torchvision.version.git_version` | ROCm/vision |
| `torchaudio.version.git_version` | ROCm/audio |
| `triton.git_version` (short hash) | triton-lang/triton |

Compare each changed commit with the `gh api .../compare` command above.
The ROCm submodules (`rocm-systems`, `rocm-libraries`, `llvm-project`) have
many commits between nightly builds. Summarize them by component, and do not
list each commit.

## Release notes template

```markdown
# Release vYY.M.P

## New Models & Model Updates
- Description of change

## Performance Improvements
- Description of change

## Docker & Environment
- Description of change

## Benchmark Configurations
- Description of change

## Other Changes
- Description of change
```
