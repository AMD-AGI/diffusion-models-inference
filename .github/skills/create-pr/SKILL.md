---
name: create-pr
description: "Create a pull request from current branch. Use when: create pr, make pr, open pull request, submit pr, pull request from branch."
argument-hint: "[issue-number]"
user-invocable: true
disable-model-invocation: false
---

# Create Pull Request

## Preflight

Complete the shared [repository skill preflight](../preflight/SKILL.md) before continuing.

## Steps

1. **Identify branch and issue**
   1.1. [Resolve repository, default branch, and current branch](./commands.md#resolve-context)
   1.2. If the user provides an issue number, or the branch matches `<type>/<number>-description`,
      treat it as an untrusted candidate and [fetch it from the resolved repository](./commands.md#fetch-issue).
   1.3. Validate that the issue title reasonably matches the branch and requested work. If it does not,
      warn the user and ask whether to use another issue or proceed without one.
   1.4. Issue linkage is optional. Never require an issue and never reuse a number from another repository.
2. **Gather branch changes**
   2.1. [List commits](./commands.md#list-commits) on branch vs the resolved target branch
   2.2. [Get diff summary](./commands.md#get-diff-summary) — files changed, insertions, deletions
   2.3. [Get full diff](./commands.md#get-full-diff) (or per-file if too large) to understand actual changes
3. **Populate template** per [template structure](./template.md)
   3.1. **Short description** — one-line summary derived from the validated issue, if any, plus the diff
   3.2. **Closes** — include `Closes #<issue_number>` only for a validated issue; otherwise omit it
   3.3. **Background** — use the issue body when linked; otherwise derive context from commits and diff
   3.4. **Goals** — use issue goals when linked; otherwise derive concrete goals from the change
   3.5. **Tasks** — compare issue goals against diff and commits:
        - For each goal, identify commits/changes that address it
        - Mark task as `[x]` if the diff shows it was completed
        - Mark task as `[ ]` if the goal is not covered by the diff
   3.6. **Tests** — ask the user what tests have been run; leave empty if none
   3.7. **Other** — scan commits and diff for changes outside the issue scope:
        - Necessary supporting changes (refactors, fixes unblocking the main work)
        - Unrelated changes bundled in the branch (note these explicitly)
        - Derive from commit messages that don't map to any issue goal
4. **Determine PR settings**
   4.1. Target branch: repository default branch — override only if user specifies
   4.2. Draft: no (default) — set `--draft` only if user says draft
   4.3. Reviewers: omit unless user specifies
5. **Present draft PR** — show title, body, target, draft status, reviewers
   5.1. Wait for user confirmation or edits
6. **Create PR** — [create pull request](./commands.md#create-pull-request)
