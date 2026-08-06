# Commands — trigger-build-core-image

## Resolve context

```bash
REPO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner')
BRANCH=$(git branch --show-current)
```

## Dispatch template

Include `--field` only for parameters the user explicitly provides. Omit everything else.

```bash
gh workflow run "build-core-image.yml" \
  --repo "$REPO" \
  --ref "$BRANCH"
```

### Optional fields (add only when user specifies)

```
  --field tag="TAG"
  --field runner="RUNNER"
  --field prebuilt_core_image_tag="PREBUILT_TAG"
  --field disable_docker_cache=true
```

## Identify the new run

Record existing workflow-dispatch run IDs immediately before dispatch:

```bash
BEFORE_IDS=$(gh run list --repo "$REPO" --workflow build-core-image.yml \
  --event workflow_dispatch --branch "$BRANCH" --limit 20 \
  --json databaseId --jq '.[].databaseId')
```

After dispatch, repeat this read-only query until it returns an ID not present in
`BEFORE_IDS`, then report its URL:

```bash
gh run list --repo "$REPO" --workflow build-core-image.yml \
  --event workflow_dispatch --branch "$BRANCH" --limit 20 \
  --json databaseId,number,status,url,createdAt
```

## Workflow inputs reference

| Input | Description | Default |
|---|---|---|
| `tag` | Image tag; short commit SHA used if empty. Untuned image gets `-temp` suffix. | *(auto)* |
| `runner` | Runner label | Workflow default |
| `prebuilt_core_image_tag` | Skip core build, build untuned image from this tag | *(empty)* |
| `disable_docker_cache` | Disable the core Docker build cache | `false` |
