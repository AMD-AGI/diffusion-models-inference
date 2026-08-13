# Commands — trigger-build-and-benchmark

## Resolve context

```bash
REPO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner')
BRANCH=$(git branch --show-current)
```

## Find latest core build

```bash
gh run list \
  --repo "$REPO" \
  --workflow build-core-image.yml \
  --branch DEFAULT_BRANCH \
  --status success \
  --limit 1 \
  --json headSha,startedAt,conclusion,url \
  --jq '.[0]'
```

Untuned image tag = first 7 chars of `headSha` + `-temp`.

## Workflow run template

Start with this command. Include optional fields only when non-empty or when
overriding a default:

```bash
gh workflow run "build-and-benchmark.yml" \
  --repo "$REPO" \
  --ref "$BRANCH" \
  --field gpu_runners="GPU_RUNNERS"
```

Supported optional fields:

```text
--field git_branch="GIT_BRANCH"
--field prebuilt_core_image_tag="CORE_TAG"
--field prebuilt_untuned_image_tag="UNTUNED_TAG"
--field run_mode="RUN_MODE"
--field miopen_find_mode="1"
--field miopen_find_enforce="3"
--field force_retuning=true
--field benchmark_flags="BENCHMARK_FLAGS"
--field collect_hipblaslt_logs=true
--field benchmark_image="BENCHMARK_IMAGE"
--field disable_docker_cache=true
```

## Identify the new run

Record existing IDs immediately before dispatch:

```bash
BEFORE_IDS=$(gh run list --repo "$REPO" --workflow build-and-benchmark.yml \
  --event workflow_dispatch --branch "$BRANCH" --limit 20 \
  --json databaseId --jq '.[].databaseId')
```

After dispatch, repeat this query until it returns an ID not present in
`BEFORE_IDS`, then report its URL:

```bash
gh run list --repo "$REPO" --workflow build-and-benchmark.yml \
  --event workflow_dispatch --branch "$BRANCH" --limit 20 \
  --json databaseId,number,status,url,createdAt
```
