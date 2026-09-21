#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

# Clean orphaned core registry-cache tags. The registry cache is deliberately
# managed independently from local BuildKit cache.

: "${CACHE_IMAGE_NAME:?CACHE_IMAGE_NAME is required}"
: "${GITHUB_TOKEN:?GITHUB_TOKEN is required}"
: "${DOCKERHUB_USERNAME:?DOCKERHUB_USERNAME is required}"
: "${DOCKERHUB_TOKEN:?DOCKERHUB_TOKEN is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# The helper path is resolved relative to this action at runtime.
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../../../scripts/cache-scope.sh"

DOCKERHUB_API_URL="${DOCKERHUB_API_URL:-https://hub.docker.com/v2}"
DOCKERHUB_API_URL="${DOCKERHUB_API_URL%/}"

if [[ "$CACHE_IMAGE_NAME" == docker.io/* ]]; then
  CACHE_IMAGE_NAME="${CACHE_IMAGE_NAME#docker.io/}"
fi

if [[ "$CACHE_IMAGE_NAME" != */* || "$CACHE_IMAGE_NAME" == */*/* ]]; then
  echo "::error::CACHE_IMAGE_NAME must be a Docker Hub namespace/repository: ${CACHE_IMAGE_NAME}" >&2
  exit 1
fi

CACHE_NAMESPACE="${CACHE_IMAGE_NAME%%/*}"
CACHE_REPOSITORY="${CACHE_IMAGE_NAME#*/}"
TAGS_URL="${DOCKERHUB_API_URL}/namespaces/${CACHE_NAMESPACE}/repositories/${CACHE_REPOSITORY}/tags"

if ! [[ "${GRACE_PERIOD_DAYS:-}" =~ ^[0-9]+$ ]]; then
  echo "::error::GRACE_PERIOD_DAYS must be a non-negative integer" >&2
  exit 1
fi

case "${MANUAL_DRY_RUN:-inherit}" in
  true|false)
    DRY_RUN="${MANUAL_DRY_RUN}"
    ;;
  inherit|'')
    DRY_RUN="${DRY_RUN_SETTING:-true}"
    ;;
  *)
    echo "::error::MANUAL_DRY_RUN must be inherit, true, or false" >&2
    exit 1
    ;;
esac

case "${DRY_RUN,,}" in
  true|false)
    DRY_RUN="${DRY_RUN,,}"
    ;;
  *)
    echo "::error::DRY_RUN_SETTING must be true or false" >&2
    exit 1
    ;;
esac

WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT

tags_file="${WORK_DIR}/tags.ndjson"
branches_file="${WORK_DIR}/branches.txt"

die() {
  echo "::error::$*" >&2
  exit 1
}

summary_file="${GITHUB_STEP_SUMMARY:-}"

echo "Core cache repository: ${CACHE_IMAGE_NAME}"
echo "Grace period: ${GRACE_PERIOD_DAYS} day(s)"
echo "Mode: $([[ "$DRY_RUN" == true ]] && echo dry-run || echo deletion)"

if ! auth_response=$(
  jq -n '{identifier: env.DOCKERHUB_USERNAME, secret: env.DOCKERHUB_TOKEN}' |
    curl --fail --silent --show-error \
      -H 'Accept: application/json' \
      -H 'Content-Type: application/json' \
      --data-binary @- \
      "${DOCKERHUB_API_URL}/auth/token"
); then
  die "Docker Hub authentication failed"
fi

if ! dockerhub_jwt=$(jq -er '.access_token // empty' <<< "$auth_response"); then
  die "Docker Hub authentication response did not contain a token"
fi

dockerhub_get() {
  printf 'header = "Authorization: Bearer %s"\nheader = "Accept: application/json"\n' "$dockerhub_jwt" |
    curl --fail --silent --show-error --config - "$1"
}

dockerhub_delete() {
  printf 'request = "DELETE"\nheader = "Authorization: Bearer %s"\nheader = "Accept: application/json"\n' "$dockerhub_jwt" |
    curl --fail --silent --show-error --config - "$1"
}

echo "Listing Docker Hub cache tags..."
next_url="${TAGS_URL}?page_size=100"
: > "$tags_file"
while [[ -n "$next_url" ]]; do
  if ! response=$(dockerhub_get "$next_url"); then
    die "Docker Hub tag listing failed"
  fi

  if ! jq -e '.results | type == "array"' >/dev/null <<< "$response"; then
    die "Docker Hub tag listing returned an invalid response"
  fi
  jq -c '.results[] | {name: .name, last_updated: .last_updated}' <<< "$response" >> "$tags_file"
  next_url=$(jq -r '.next // empty' <<< "$response")
done

echo "Listing current repository branches..."
if ! GH_TOKEN="$GITHUB_TOKEN" gh api --paginate \
  -H 'X-GitHub-Api-Version: 2022-11-28' \
  "repos/${GITHUB_REPOSITORY}/branches?per_page=100" \
  --jq '.[].name' > "$branches_file"; then
  die "GitHub branch discovery failed"
fi

declare -A protected_tags=()
protected_tags['core-main']=1
live_branch_count=0
while IFS= read -r branch; do
  [[ -n "$branch" ]] || continue
  live_branch_count=$((live_branch_count + 1))
  scope=$(slug "$branch")
  if [[ -n "$scope" ]]; then
    protected_tags["core-${scope}"]=1
  else
    echo "::warning::Could not derive a cache scope from live branch '${branch}'"
  fi
done < "$branches_file"

now=$(date -u +%s)
grace_seconds=$((GRACE_PERIOD_DAYS * 86400))
cutoff=$((now - grace_seconds))

orphan_count=0
recent_count=0
skipped_count=0
deleted_count=0
failure_count=0
candidate_names=()
skipped_names=()
failed_names=()

while IFS= read -r tag_record; do
  tag_name=$(jq -r '.name // empty' <<< "$tag_record")
  last_updated=$(jq -r '.last_updated // empty' <<< "$tag_record")

  [[ "$tag_name" == core-* ]] || continue
  [[ -n "${protected_tags[$tag_name]+x}" ]] && continue

  orphan_count=$((orphan_count + 1))
  candidate_names+=("$tag_name")

  if [[ -z "$last_updated" ]]; then
    skipped_count=$((skipped_count + 1))
    skipped_names+=("${tag_name} (missing last_updated)")
    echo "::warning::Skipping ${tag_name}: missing last_updated metadata"
    continue
  fi

  if ! updated_at=$(date -u -d "$last_updated" +%s 2>/dev/null); then
    skipped_count=$((skipped_count + 1))
    skipped_names+=("${tag_name} (invalid last_updated)")
    echo "::warning::Skipping ${tag_name}: invalid last_updated '${last_updated}'"
    continue
  fi

  if (( updated_at > cutoff )); then
    recent_count=$((recent_count + 1))
    echo "  keep ${tag_name}: younger than ${GRACE_PERIOD_DAYS} day(s)"
    continue
  fi

  encoded_tag=$(jq -rn --arg tag "$tag_name" '$tag | @uri')
  delete_url="${TAGS_URL}/${encoded_tag}"
  if [[ "$DRY_RUN" == true ]]; then
    echo "  dry-run delete ${tag_name}"
    continue
  fi

  if dockerhub_delete "$delete_url" >/dev/null; then
    deleted_count=$((deleted_count + 1))
    echo "  deleted ${tag_name}"
  else
    failure_count=$((failure_count + 1))
    failed_names+=("$tag_name")
    echo "::error::Failed to delete ${tag_name}"
  fi
done < "$tags_file"

{
  echo "### Core registry-cache cleanup"
  echo
  echo "- Mode: \`${DRY_RUN}\`"
  echo "- Repository: \`${CACHE_IMAGE_NAME}\`"
  echo "- Grace period: \`${GRACE_PERIOD_DAYS}\` day(s)"
  echo "- Live branches: \`${live_branch_count}\`"
  echo "- Orphan candidates: \`${orphan_count}\`"
  echo "- Recent candidates retained: \`${recent_count}\`"
  echo "- Candidates skipped: \`${skipped_count}\`"
  echo "- Tags deleted: \`${deleted_count}\`"
  echo "- Deletion failures: \`${failure_count}\`"
  if ((${#candidate_names[@]} > 0)); then
    echo
    echo "Candidates:"
    printf '%s\n' "${candidate_names[@]}" | sed 's/^/- /'
  fi
  if ((${#skipped_names[@]} > 0)); then
    echo
    echo "Skipped:"
    printf '%s\n' "${skipped_names[@]}" | sed 's/^/- /'
  fi
  if ((${#failed_names[@]} > 0)); then
    echo
    echo "Deletion failures:"
    printf '%s\n' "${failed_names[@]}" | sed 's/^/- /'
  fi
} | if [[ -n "$summary_file" ]]; then
  cat >> "$summary_file"
else
  cat
fi

if (( failure_count > 0 )); then
  exit 1
fi
