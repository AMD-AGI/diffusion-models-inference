---
name: preflight
user-invocable: false
disable-model-invocation: false
description: "Shared prerequisite checks for repository skills. Used by other skills before running GitHub CLI workflows."
---

# Repository Skill Preflight

Run these checks before the calling skill performs any other steps.

## Checks

1. **GitHub CLI** — [verify `gh` is installed](./commands.md#github-cli)
2. **Authentication** — [verify the current `gh` authentication](./commands.md#authentication)
3. **Repository** — [resolve the repository from the current checkout](./commands.md#repository)

Stop at the first failed check and report its remediation. Continue with the
calling skill only after every check succeeds.

## Extension Rule

Add future checks here only when they apply broadly to repository skills. Keep
task-specific input validation in the skill that owns the task.