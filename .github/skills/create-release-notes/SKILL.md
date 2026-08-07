---
name: create-release-notes
description: "Generate release notes from merged PRs between releases. Use when: release notes, create release notes, changelog, what changed, PR summary, release summary, diff between versions, categorize changes."
---

# Generate Release Notes

## Preflight

Complete the shared [repository skill preflight](../preflight/SKILL.md) before continuing.

## Steps

1. **Determine version range** — ask which release; default to upcoming
   1.1. [List recent tags](./commands.md#list-recent-tags) to identify previous tag (`FROM_TAG`)
   1.2. [Resolve repository and default branch](./commands.md#resolve-context)
   1.3. Set `TO_REF`: existing tag for a past release, repository default branch for upcoming
2. **Collect changes in range** — use first-parent commits between `FROM_TAG..TO_REF`
   2.1. [List release commits](./commands.md#list-release-commits)
   2.2. [Resolve PRs associated with each commit](./commands.md#resolve-associated-prs)
   2.3. If no public PR is associated, retain the commit SHA, subject, and changed files. Never
        convert a historical `#N` subject into a public PR link without API confirmation.
   2.4. [Diff dependency commit values](./commands.md#diff-dependency-commits) in `docker/Dockerfile.ci`
   2.5. For each changed commit hash, [look up upstream changes](./commands.md#look-up-upstream-changes) using the repo mapping
3. **Categorize each PR** by changed file paths per [categorization rules](./categorization.md)
   3.1. Included (affect image): New Model, Performance, Docker/Environment, Benchmark Configs, Libraries
   3.2. Excluded (don't affect the ROCm image): CI, Docs, Scripts, MIOpen, MAD
   3.3. PRs touching both: categorize by primary purpose; include if image-affecting
4. **Review categorization with user** — present included/excluded tables, ask for moves
5. **Generate release notes** — group included PRs by category
   5.1. Write concise descriptions (not raw PR titles)
   5.2. Include upstream dependency changes from step 2.4 under relevant categories
      5.3. Link confirmed public PRs; use short commit links or SHAs for unresolved historical changes;
         group related changes and omit empty categories
   5.4. Format per [release notes template](./commands.md#release-notes-template)
6. **Present and refine** — ask for edits, reordering, highlights
7. **Output** — provide final markdown; suggest `create-release` skill to publish

## Input Reference

See [categorization.md](./categorization.md) for the file path patterns
used to categorize PRs.
