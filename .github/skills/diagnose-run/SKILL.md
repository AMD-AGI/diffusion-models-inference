---
name: diagnose-run
description: "Diagnose a build-and-benchmark workflow run. Use when: check run, diagnose run, analyze run, what failed, run status, debug workflow, why did it fail, check build."
---

# Analyze Workflow Run

## Steps

1. **Identify the run**
   1.0. [Resolve the repository](./commands.md#resolve-context)
   1.1. User provides run ID or URL — extract the numeric run ID
   1.2. User provides a branch name — [find latest run for branch](./commands.md#latest-run-for-branch)
   1.3. If neither provided, [list recent runs](./commands.md#list-recent-runs) and ask which one
2. **Get run status and identity** — [view run summary](./commands.md#view-run-summary), retaining
   both the database ID used by `gh run view` and the workflow run number used in artifact names
   2.1. If all jobs succeeded → report success, show duration, done
   2.2. If any jobs failed or cancelled → continue to step 3
3. **Identify failed jobs** — [get job details](./commands.md#get-job-details)
   3.1. List each job with its status (success/failure/cancelled/skipped)
   3.2. For failed jobs, note which step failed
4. **Fetch failure logs** — [get failed logs](./commands.md#get-failed-logs)
   4.1. Scan for error patterns: stack traces, `Error:`, `exit code`, OOM, timeout
   4.2. Summarize root cause per failed job
   4.3. If a benchmark experiment failed (log shows `Experiment <name> failed to complete`):
        - Extract the experiment name and architecture from the error line
      - [Download benchmark artifact](./commands.md#download-benchmark-artifact) (`<run_number>-benchmarks-<arch>`)
      - If the artifact exists, read `<experiment_name>/stderr.txt` for the actual error
      - If it is missing or expired, report that limitation and use the available run log
5. **Report** — present findings:
   5.1. Overall status + duration
   5.2. Per-job breakdown (pass/fail/skip)
   5.3. For failures: failed step, error summary, relevant log excerpt
