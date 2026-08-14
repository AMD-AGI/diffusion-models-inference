# Commands — preflight

## GitHub CLI

```bash
command -v gh >/dev/null 2>&1
```

If this fails, stop and direct the user to the GitHub CLI installation page:
<https://cli.github.com/>.

## Authentication

```bash
gh auth status >/dev/null 2>&1
```

If this fails, stop and ask the user to run:

```bash
gh auth login
```

Do not start the interactive login flow on the user's behalf.

## Repository

```bash
REPO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner')
```

If this fails or returns an empty value, stop and report that the current
checkout could not be resolved to a GitHub repository.