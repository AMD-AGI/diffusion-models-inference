# Commands — get-benchmark-results

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
  --json databaseId,number,displayTitle,status,conclusion,startedAt,url
```

## Latest run for branch

```bash
gh run list \
  --repo "$REPO" \
  --workflow build-and-benchmark.yml \
  --branch BRANCH_NAME \
  --limit 1 \
  --json databaseId,number,displayTitle,status,conclusion,startedAt,url
```

## Get job details

```bash
gh run view RUN_ID \
  --repo "$REPO" \
  --json jobs \
  --jq '.jobs[] | select(.name | test("tuning and benchmarking")) | {name, status, conclusion}'
```

## Get benchmark logs

Fetch the tuning-and-benchmark job logs and filter to current benchmark output:

```bash
gh run view RUN_ID \
  --repo "$REPO" \
  --log | grep -E "(Median latency|failed to complete|Error:|Wall-clock|Experiment|Time \(s\)|---)"
```

If multiple runners are present, filter by the job name prefix (for example,
`gfx950 tuning and benchmarking`):

```bash
gh run view RUN_ID \
  --repo "$REPO" \
  --log | grep "RUNNER tuning and benchmarking" | \
  grep -E "(Median latency|failed to complete|Error:|Wall-clock|Experiment|Time \(s\)|---)"
```

## Compare two runs

Run the job and log commands independently for `CURRENT_RUN_ID` and
`COMPARISON_RUN_ID`. Match rows by full job name and experiment name. Do not
infer a comparison run from a single workflow execution.
