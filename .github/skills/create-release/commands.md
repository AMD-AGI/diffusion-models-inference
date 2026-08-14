# Commands — create-release

## Resolve context

```bash
REPO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner')
DEFAULT_BRANCH=$(gh repo view "$REPO" --json defaultBranchRef --jq '.defaultBranchRef.name')
```

## List recent tags

```bash
git tag --list 'v*' --sort=-version:refname | head -10
```

## Verify tag and release

Both commands must report no match before creation:

```bash
git tag --list 'TAG'
git ls-remote --exit-code --tags origin 'refs/tags/TAG' 2>/dev/null || true
gh release view TAG --repo "$REPO" --json tagName 2>/dev/null || true
```

## Create release auto notes

Published (default):

```bash
gh release create TAG \
  --repo "$REPO" \
  --title "TITLE" \
  --target TARGET \
  --generate-notes \
  --notes-start-tag PREVIOUS_TAG
```

## Create release auto notes draft

```bash
gh release create TAG \
  --repo "$REPO" \
  --title "TITLE" \
  --target TARGET \
  --draft \
  --generate-notes \
  --notes-start-tag PREVIOUS_TAG
```

## Create release explicit notes

```bash
gh release create TAG \
  --repo "$REPO" \
  --title "TITLE" \
  --target TARGET \
  --notes "RELEASE_NOTES_MARKDOWN"
```

## Create release explicit notes draft

```bash
gh release create TAG \
  --repo "$REPO" \
  --title "TITLE" \
  --target TARGET \
  --draft \
  --notes "RELEASE_NOTES_MARKDOWN"
```

## Publish draft

```bash
gh release edit TAG --repo "$REPO" --draft=false
```

## View release URL

```bash
gh release view TAG --repo "$REPO" --json url --jq '.url'
```
