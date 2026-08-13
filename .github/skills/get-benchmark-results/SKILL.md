---
name: get-benchmark-results
description: "Get benchmark results from a workflow run. Use when: benchmark results, get benchmarks, latency numbers, run results, performance results, compare benchmarks."
argument-hint: "<run-id|run-url|branch> [comparison-run-id|run-url]"
user-invocable: true
disable-model-invocation: false
---

# Get Benchmark Results

## Preflight

Complete the shared [repository skill preflight](../preflight/SKILL.md) before continuing.

## Steps

1. **Identify the run**
   1.0. [Resolve the repository](./commands.md#resolve-context)
   1.1. User provides run ID or URL — extract the numeric run ID
   1.2. User provides a branch name — [find latest run for branch](./commands.md#latest-run-for-branch)
   1.3. If neither provided, [list recent runs](./commands.md#list-recent-runs) and ask which one
2. **Verify benchmarks ran** — [get job details](./commands.md#get-job-details)
   2.1. Find the `tune-and-benchmark` job(s) — one per architecture
   2.2. If job failed/skipped → report that and stop
3. **Fetch benchmark logs** — [get benchmark logs](./commands.md#get-benchmark-logs)
4. **Extract results** — parse the log output for:
   4.1. Per-experiment latencies — lines matching `Median latency for <name>: <seconds> seconds`
   4.2. Failed experiments — lines matching `failed` or `Error`
   4.3. Optional wall-clock summary — include the `Experiment / Time (s)` table only when present
5. **Present results** — separate results by runner/job and show experiment, median latency, and status
6. **Compare runs** (only when the user provides two runs)
   6.1. Fetch both runs independently and match by runner/job plus experiment name
   6.2. Report current, comparison, delta seconds, and delta percent where both medians exist
   6.3. Delta percent = `(current - comparison) / comparison * 100`; negative is faster
   6.4. Report unmatched or failed experiments without manufacturing a numeric comparison
