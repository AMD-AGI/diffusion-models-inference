---
name: trigger-build-core-image
description: "Maintainer-only core image workflow dispatch. Use when: build core image, trigger core build, rebuild core, core image build, start core build."
---

# Trigger Core Image Build

## Preflight

Complete the shared [repository skill preflight](../preflight/SKILL.md) before continuing.

## Steps

1. **Resolve defaults**
   1.1. [Resolve repository and current branch](./commands.md#resolve-context)
   1.2. Tag: omit (workflow derives it from short SHA)
   1.3. Runner: omit to use the workflow default
   1.4. `prebuilt_core_image_tag`: omit
   1.5. `disable_docker_cache`: false
2. **Apply user overrides** — only include fields the user explicitly provides
3. **Validate** — branch is non-empty, booleans are `true` or `false`, and tag values contain no whitespace
4. **Build command** — [dispatch template](./commands.md#dispatch-template); include `--field` only for overrides
5. **Present and confirm** — show branch and effective overrides; wait for explicit confirmation
6. **Execute and identify run** — record existing run IDs, dispatch, then [find the new run](./commands.md#identify-the-new-run)
