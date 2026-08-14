---
name: trigger-build-and-benchmark
description: "Maintainer-only Build and Benchmark xDiT workflow dispatch. Use when: release build, trigger build, run benchmarks, dispatch workflow, start CI, benchmark image, MIOpen tuning, trigger pipeline, build-and-benchmark."
argument-hint: "[profile] [workflow-inputs]"
user-invocable: true
disable-model-invocation: false
---

# Trigger Build and Benchmark Workflow

## Preflight

Complete the shared [repository skill preflight](../preflight/SKILL.md) before continuing.

## Steps

1. **Resolve context** — [resolve repository and current branch](./commands.md#resolve-context)
2. **Determine profile** — select the closest [current run profile](./run-profiles.md), then apply user overrides
3. **Determine image source** — follow [image source precedence](./notes.md#1-image-source-precedence)
4. **Collect inputs** — use the exact defaults and choices in [workflow inputs](./workflow-inputs.md)
   4.1. `gpu_runners` is a comma-separated list of self-hosted runner labels; entries also name jobs and artifacts
   4.2. Leave `git_branch` empty to build the dispatch ref, or set it to build another ref
   4.3. Include boolean fields only when overriding their workflow defaults
5. **Validate** using [validation rules](./notes.md#3-validation-rules)
6. **Resolve runner-label ambiguity** — apply the [overlap confirmation rules](./notes.md#5-runner-label-overlap)
   6.1. If an overlap is detected, explain it and ask whether both matrix entries are intentional
   6.2. Do not build the command until the user confirms both labels or chooses which one to keep
7. **Build command** — use the [workflow run template](./commands.md#workflow-run-template) and omit empty optional fields
8. **Present and confirm** [^2](./notes.md#2-confirmation-checklist) — wait for explicit confirmation
9. **Execute and identify run** — record existing run IDs, dispatch, then [find the new run](./commands.md#identify-the-new-run)

**Validation** [^3](./notes.md#3-validation-rules)
