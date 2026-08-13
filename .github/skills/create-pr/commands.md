# Commands — create-pr

## Resolve context

```bash
REPO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner')
DEFAULT_BRANCH=$(gh repo view "$REPO" --json defaultBranchRef --jq '.defaultBranchRef.name')
CURRENT_BRANCH=$(git branch --show-current)
```

## Fetch issue

```bash
gh issue view ISSUE_NUMBER \
  --repo "$REPO" \
  --json title,body,labels,assignees
```

## List commits

Commits on the current branch not in the target branch:

```bash
git log "TARGET_BRANCH..HEAD" --oneline
```

For more detail (message + files):

```bash
git log "TARGET_BRANCH..HEAD" --format="%h %s" --name-only
```

## Get diff summary

```bash
git diff "TARGET_BRANCH...HEAD" --stat
```

## Get full diff

```bash
git diff "TARGET_BRANCH...HEAD"
```

If too large, diff per file:

```bash
git diff "TARGET_BRANCH...HEAD" -- PATH
```

## Create pull request

```bash
gh pr create \
  --repo "$REPO" \
  --base TARGET_BRANCH \
  --title "TITLE" \
  --body "BODY"
```

With reviewers:

```bash
gh pr create \
  --repo "$REPO" \
  --base TARGET_BRANCH \
  --title "TITLE" \
  --body "BODY" \
  --reviewer "USER1,USER2"
```

As draft:

```bash
gh pr create \
  --repo "$REPO" \
  --base TARGET_BRANCH \
  --title "TITLE" \
  --body "BODY" \
  --draft
```
