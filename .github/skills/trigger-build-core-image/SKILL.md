---
name: trigger-build-core-image
description: "Maintainer-only core image workflow dispatch. Use when: build core image, trigger core build, rebuild core, core image build, start core build."
argument-hint: "[branch] [workflow-inputs]"
user-invocable: true
disable-model-invocation: false
---

# Trigger Core Image Build

## Preflight

Complete the shared [repository skill preflight](../preflight/SKILL.md) before continuing.

## Steps

1. **Resolve defaults**
   1.1. [Resolve repository and current branch](./commands.md#resolve-context)
   1.2. Framework: omit (workflow defaults to `xdit`); accept `sgld` or `vllmo` when the user asks for SGL-D or vLLM-Omni
   1.3. Tag: omit (workflow derives it from short SHA)
   1.4. Runner: omit to use the workflow default
   1.5. `prebuilt_core_image_tag`: omit
   1.6. `disable_docker_cache`: false
   1.7. `cache_scope`: omit (derived from the branch, which already isolates the mainline cache)
2. **Apply user overrides** — only include fields the user explicitly provides
3. **Validate** — branch is non-empty, `framework` is `xdit`, `sgld`, or `vllmo`, `prebuilt_core_image_tag` is used only with `xdit`, booleans are `true` or `false`, and tag values contain no whitespace
4. **Build command** — [dispatch template](./commands.md#dispatch-template); include `--field` only for overrides
5. **Present and confirm** — show branch and effective overrides; wait for explicit confirmation
6. **Execute and identify run** — record existing run IDs, dispatch, then [find the new run](./commands.md#identify-the-new-run)
