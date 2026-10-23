# Commands — trigger-build-and-benchmark

## Resolve context

```bash
REPO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner')
BRANCH=$(git branch --show-current)
```

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
--field base_image="BASE_IMAGE"
--field use_latest_main_core=true
--field image_tag="IMAGE_TAG"
--field rebuild=true
--field run_miopen_tuning=false
--field run_benchmarks=false
--field build_final=false
--field create_miopen_db_branch=true
--field force_retuning=true
--field benchmark_flags="BENCHMARK_FLAGS"
--field collect_hipblaslt_logs=true
--field force_determinism_check=true
--field disable_docker_cache=true
--field build_runner="BUILD_RUNNER"
--field cache_scope="CACHE_SCOPE"
--field docker_build_args=$'KEY=VALUE\nOTHER_KEY=OTHER_VALUE'
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
