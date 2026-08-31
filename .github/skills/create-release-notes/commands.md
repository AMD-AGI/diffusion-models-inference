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
Include `*_COMMIT`, `ROCM_RELEASE_ID`, and `ROCM_DEB_SERIES`:

```bash
git diff FROM_TAG..TO_REF -- docker/Dockerfile.ci | \
  grep -E '^[+-]ARG\s+(\w+_COMMIT|ROCM_RELEASE_ID|ROCM_DEB_SERIES)=' | sort
```

## Look up upstream changes

For each changed commit hash (`OLD_HASH` → `NEW_HASH`), query the upstream repo
to see what was added. Use the repo mapping below to resolve the GitHub org/repo.
Record `ROCM_RELEASE_ID` and `ROCM_DEB_SERIES` changes as TheRock nightly deb
snapshot updates; they are not Git commit hashes.

```bash
gh api repos/OWNER/REPO/compare/OLD_HASH...NEW_HASH \
  --jq '.commits[] | {sha: .sha[0:7], message: (.commit.message | split("\n")[0])}'
```

### Repo mapping

| ARG name | GitHub repo |
|---|---|
| THEROCK_COMMIT | ROCm/TheRock |
| TRITON_COMMIT | triton-lang/triton |
| PYTORCH_COMMIT | pytorch/pytorch |
| PYTORCH_VISION_COMMIT | pytorch/vision |
| PYTORCH_AUDIO_COMMIT | pytorch/audio |
| PYTORCH_CODEC_COMMIT | meta-pytorch/torchcodec |
| AITER_COMMIT | ROCm/aiter |
| LONGCONTEXTATTENTION_COMMIT | feifeibear/long-context-attention |
| DIFFUSERS_COMMIT | huggingface/diffusers |
| DISTVAE_COMMIT | xdit-project/DistVAE |
| XDIT_COMMIT | xdit-project/xDiT |
| AO_COMMIT | pytorch/ao |
| ARBITER_COMMIT | fal-ai/arbiter |

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
