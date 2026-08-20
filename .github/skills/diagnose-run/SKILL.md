---
name: diagnose-run
description: "Diagnose a GitHub Actions workflow run. Use when: check run, diagnose run, analyze run, what failed, run status, debug workflow, why did it fail, check build."
argument-hint: "<workflow> [run-id|run-url|branch]"
user-invocable: true
disable-model-invocation: false
---

# Analyze Workflow Run

## Preflight

Complete the shared [repository skill preflight](../preflight/SKILL.md) before continuing.

## Steps

1. **Identify the workflow**
   1.0. [Resolve the repository](./commands.md#resolve-context)
   1.1. [List available workflows](./commands.md#list-available-workflows)
   1.2. Exclude files ending in `-reusable.yml`; they cannot be selected directly by users
   1.3. If the user did not provide a workflow, show the available workflows and ask which one
   1.4. Validate the provided workflow against the available workflow file names or their `name:` values
2. **Identify the run within the selected workflow**
   2.1. User provides run ID or URL — extract the numeric run ID and verify the run belongs to
        the selected workflow using [view run summary](./commands.md#view-run-summary)
   2.2. User provides a branch name — [find latest run for branch](./commands.md#latest-run-for-branch)
   2.3. If none provided, [list recent runs](./commands.md#list-recent-runs) for the selected
        workflow and ask which run to diagnose
3. **Get run status and identity** — [view run summary](./commands.md#view-run-summary), retaining
   both the database ID used by `gh run view` and the workflow run number used in artifact names
   3.1. If all jobs succeeded → report success, show duration, done
   3.2. If any jobs failed or cancelled → continue to step 4
4. **Identify failed jobs** — [get job details](./commands.md#get-job-details)
   4.1. List each job with its status (success/failure/cancelled/skipped)
   4.2. For failed jobs, note which step failed
5. **Fetch failure logs** — [get failed logs](./commands.md#get-failed-logs)
   5.1. Scan for error patterns: stack traces, `Error:`, `exit code`, OOM, timeout
   5.2. Summarize root cause per failed job
   5.3. If a benchmark experiment failed (log shows `Experiment <name> failed to complete`):
        - Extract the experiment name and architecture from the error line
      - [Download benchmark artifact](./commands.md#download-benchmark-artifact) (`<run_number>-benchmarks-<arch>`)
      - If the artifact exists, read `<experiment_name>/stderr.txt` for the actual error
      - If it is missing or expired, report that limitation and use the available run log
6. **Report** — present findings:
   6.1. Overall status + duration
   6.2. Per-job breakdown (pass/fail/skip)
   6.3. For failures: failed step, error summary, relevant log excerpt
