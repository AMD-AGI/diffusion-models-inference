---
name: orchestrate-release
description: "Maintainer-only release creation and build dispatch. Use when: orchestrate release, release version, full release, end to end release, release pipeline, release v, cut and build release."
argument-hint: "<vYY.M.P>"
user-invocable: true
disable-model-invocation: false
---

# Release Version — Orchestration

References `create-release`, `create-release-notes`, and `trigger-build-and-benchmark` skills — does
not duplicate their logic.

## Preflight

Complete the shared [repository skill preflight](../preflight/SKILL.md) before continuing.

## Phase 1: Resolve and validate [^1](./notes.md#1-single-prompt)

1. **Resolve repository context** — [repository and default branch](./commands.md#resolve-context)
2. **Determine release version**
   1.1. Extract tag from user message (convention: `vYY.M.P`)
   1.2. [Verify local tag and remote release do not exist](./commands.md#verify-tag-and-release)
3. **Collect release inputs in one prompt**
   3.1. Target branch (default: repository default branch)
   3.2. Previous tag (default: latest version tag)
   3.3. Draft? (default: no)
   3.4. Release-note edits or exclusions
   3.5. Optional known-good `prebuilt_untuned_image_tag`; omit for a full source build
   3.6. `gpu_runners` (default from the workflow: `gfx942,gfx950`)
   3.7. Any supported build overrides from `trigger-build-and-benchmark`
4. **Generate release notes** — follow `create-release-notes` with `FROM_TAG` = previous tag
   and `TO_REF` = target branch
5. **Construct and validate all commands**
   5.1. Follow `create-release` using explicit generated notes
   5.2. Follow `trigger-build-and-benchmark` using the prebuilt untuned profile when a tag was supplied,
        otherwise the full standard profile
   5.3. Published release build ref = release tag; draft build ref = target branch
   5.4. Record current workflow run IDs for deterministic capture
6. **Present one confirmation** — show release tag, target, draft status, notes, image source,
   runners, build overrides, and both commands

## Phase 2: Execute (unattended — no further prompts) [^2](./notes.md#2-unattended-execution)

7. **Create GitHub release** — execute the confirmed `create-release` command
8. **Trigger release build** — execute the confirmed `trigger-build-and-benchmark` command
9. **Capture and report the run** — [identify the new run](./commands.md#identify-the-new-run)
10. **Handle partial failure** [^3](./notes.md#3-partial-failure)
    10.1. Never retry release creation without checking whether it succeeded
    10.2. Report completed mutations, the failing command, and an exact recovery command
