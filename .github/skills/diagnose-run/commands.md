# Commands — diagnose-run

## Resolve context

```bash
REPO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner')
```

## List recent runs

```bash
gh run list \
  --repo "$REPO" \
  --workflow build-and-benchmark.yml \
  --limit 5 \
  --json databaseId,number,displayTitle,status,conclusion,startedAt,updatedAt,url
```

## Latest run for branch

```bash
gh run list \
  --repo "$REPO" \
  --workflow build-and-benchmark.yml \
  --branch BRANCH_NAME \
  --limit 1 \
  --json databaseId,number,displayTitle,status,conclusion,startedAt,updatedAt,url
```

## View run summary

```bash
gh run view RUN_ID \
  --repo "$REPO" \
  --json databaseId,number,displayTitle,status,conclusion,startedAt,updatedAt,url
```

## Get job details

```bash
gh run view RUN_ID \
  --repo "$REPO" \
  --json jobs \
  --jq '.jobs[] | {name, status, conclusion, startedAt, completedAt, steps: [.steps[] | select(.conclusion == "failure") | {name, conclusion}]}'
```

## Get failed logs

```bash
gh run view RUN_ID \
  --repo "$REPO" \
  --log-failed
```

If output is too large, filter to a specific failed job:

```bash
gh run view RUN_ID \
  --repo "$REPO" \
  --log-failed | grep -A 50 "JOB_NAME"
```

## Download benchmark artifact

When a benchmark experiment fails, the run log only shows which experiment
failed. The actual error is in the artifact.

Artifact naming: `<run_number>-benchmarks-<runner>` where `runner` is the matrix
value from `gpu_runners` (for example, `352-benchmarks-gfx942`). List artifacts
first because they may have expired:

```bash
gh api "repos/$REPO/actions/runs/RUN_ID/artifacts" \
  --jq '.artifacts[] | {name, expired}'
```

```bash
gh run download RUN_ID \
  --repo "$REPO" \
  --name RUN_NUMBER-benchmarks-RUNNER \
  --dir /tmp/benchmarks-RUNNER
```

Then read the stderr for the failing experiment:

```bash
cat /tmp/benchmarks-RUNNER/EXPERIMENT_NAME/stderr.txt
```

The experiment name comes from the error line in the run log:
`Experiment <name> failed to complete`
