# Commands — orchestrate-release

## Resolve context

```bash
REPO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner')
DEFAULT_BRANCH=$(gh repo view "$REPO" --json defaultBranchRef --jq '.defaultBranchRef.name')
```

## List recent tags

```bash
git tag --list 'v*' --sort=-version:refname | head -5
```

## Verify tag and release

```bash
git tag --list 'TAG_NAME'
git ls-remote --exit-code --tags origin 'refs/tags/TAG_NAME' 2>/dev/null || true
gh release view TAG_NAME --repo "$REPO" --json tagName 2>/dev/null || true
```

Both commands must report no match before release creation.

## Update release notes

```bash
gh release edit TAG \
  --repo "$REPO" \
  --notes "GENERATED_RELEASE_NOTES"
```

Prefer passing generated notes to `gh release create --notes` directly. Use this
edit command only to recover from notes that were created incorrectly.

## Identify the new run

Record matching IDs immediately before dispatch, using the build ref selected in
phase 1:

```bash
BEFORE_IDS=$(gh run list --repo "$REPO" --workflow build-and-benchmark.yml \
  --event workflow_dispatch --branch "BUILD_REF" --limit 20 \
  --json databaseId --jq '.[].databaseId')
```

After dispatch, repeat the query until a new ID appears, then report the exact
run number and URL:

```bash
gh run list --repo "$REPO" --workflow build-and-benchmark.yml \
  --event workflow_dispatch --branch "BUILD_REF" --limit 20 \
  --json databaseId,number,status,url,createdAt
```
