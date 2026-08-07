---
name: create-release
description: "Create a GitHub release with tag and notes. Use when: create release, tag release, publish release, draft release, new version, cut release."
argument-hint: "<vYY.M.P>"
user-invocable: true
disable-model-invocation: false
---

# Create GitHub Release

## Preflight

Complete the shared [repository skill preflight](../preflight/SKILL.md) before continuing.

## Steps

1. **Determine the tag** — convention: `vYY.M.P` (e.g., `v26.5.1`)
   1.1. [List recent tags](./commands.md#list-recent-tags) to suggest next logical tag
   1.2. [Resolve repository and default branch](./commands.md#resolve-context)
   1.3. [Verify the tag and release do not exist](./commands.md#verify-tag-and-release)
   1.4. Ask target commit/branch (default: repository default branch)
   1.5. Ask draft or published (default: published) [^1](./notes.md#1-draft-vs-published)
2. **Determine release notes** — offer three options:
   2.1. Auto-generate from commits — [find previous tag](./commands.md#list-recent-tags) for `--notes-start-tag`
   2.2. User provides markdown directly
   2.3. Use `create-release-notes` skill first, then pass output here
3. **Build the command** — pick the matching template:
   3.1. Auto-generated notes → [create-release-auto](./commands.md#create-release-auto-notes)
   3.2. Auto-generated notes, draft → [create-release-auto-draft](./commands.md#create-release-auto-notes-draft)
   3.3. Explicit notes → [create-release-explicit](./commands.md#create-release-explicit-notes)
   3.4. Explicit notes, draft → [create-release-explicit-draft](./commands.md#create-release-explicit-notes-draft)
4. **Present command** — show to user, wait for explicit confirmation
5. **Execute** — run command, then [show the release URL](./commands.md#view-release-url)
6. **Publish draft** (optional) — [publish-draft](./commands.md#publish-draft)
