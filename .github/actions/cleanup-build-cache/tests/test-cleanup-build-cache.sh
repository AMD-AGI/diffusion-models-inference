#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "${SCRIPT_DIR}/../../../.." && pwd)
CLEANUP_SCRIPT="${REPO_ROOT}/.github/actions/cleanup-build-cache/scripts/cleanup-build-cache.sh"

# The helper path is resolved relative to the repository at runtime.
# shellcheck disable=SC1091
source "${REPO_ROOT}/.github/scripts/cache-scope.sh"

assert_contains() {
  local expected="$1"
  local actual="$2"
  if ! grep -Fqx "$expected" <<< "$actual"; then
    printf 'Expected to find exact line: %s\nOutput:\n%s\n' "$expected" "$actual" >&2
    exit 1
  fi
}

assert_not_contains() {
  local unexpected="$1"
  local actual="$2"
  if grep -Fqx "$unexpected" <<< "$actual"; then
    printf 'Did not expect exact line: %s\nOutput:\n%s\n' "$unexpected" "$actual" >&2
    exit 1
  fi
}

[[ "$(slug 'feature/Foo')" == 'feature-foo' ]]
[[ "$(slug 'refs/heads/Feature/Foo')" == 'feature-foo' ]]

mock_dir=$(mktemp -d)
trap 'rm -rf "$mock_dir"' EXIT

cat > "${mock_dir}/curl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

url="${!#}"
delete_log="${MOCK_DELETE_LOG:?MOCK_DELETE_LOG is required}"
arg_log="${MOCK_ARG_LOG:?MOCK_ARG_LOG is required}"
printf '%s\n' "$@" >> "$arg_log"

if [[ "$url" == */auth/token ]]; then
  cat >/dev/null
  printf '{"access_token":"test-token"}\n'
elif [[ "$url" == 'https://hub.docker.com/v2/namespaces/amdsiloai/repositories/pytorch-xdit-buildcache/tags?page_size=100' ]]; then
  request_config=$(cat)
  if [[ "$request_config" != *'Authorization: Bearer test-token'* ||
    "$request_config" != *'Accept: application/json'* ]]; then
    echo 'Expected Bearer authorization in curl config' >&2
    exit 23
  fi
  cat <<'JSON'
{"next":null,"results":[
  {"name":"core-main","last_updated":"2020-01-01T00:00:00Z"},
  {"name":"core-feature-live","last_updated":"2020-01-01T00:00:00Z"},
  {"name":"core-feature-foo","last_updated":"2020-01-01T00:00:00Z"},
  {"name":"core-deleted","last_updated":"2020-01-01T00:00:00Z"},
  {"name":"core-custom","last_updated":"2020-01-01T00:00:00Z"},
  {"name":"core-fail","last_updated":"2020-01-01T00:00:00Z"},
  {"name":"core-recent","last_updated":"2099-01-01T00:00:00Z"},
  {"name":"core-bad","last_updated":"not-a-date"},
  {"name":"image-tag","last_updated":"2020-01-01T00:00:00Z"}
]}
JSON
elif [[ "$url" == */tags/core-fail ]]; then
  request_config=$(cat)
  if [[ "$request_config" != *'Authorization: Bearer test-token'* ||
    "$request_config" != *'Accept: application/json'* ]]; then
    echo 'Expected Bearer authorization in curl config' >&2
    exit 23
  fi
  printf '%s\n' "$url" >> "$delete_log"
  exit 22
elif [[ "$url" == */tags/* ]]; then
  request_config=$(cat)
  if [[ "$request_config" != *'Authorization: Bearer test-token'* ||
    "$request_config" != *'Accept: application/json'* ]]; then
    echo 'Expected Bearer authorization in curl config' >&2
    exit 23
  fi
  printf '%s\n' "$url" >> "$delete_log"
else
  cat >/dev/null
  printf '{}\n'
fi
EOF

cat > "${mock_dir}/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${MOCK_GH_FAILURE:-false}" == true ]]; then
  exit 1
fi
printf '%s\n' main feature/live feature/foo
EOF

chmod +x "${mock_dir}/curl" "${mock_dir}/gh"

run_cleanup() {
  local dry_run="$1"
  local summary_file="$2"
  PATH="${mock_dir}:${PATH}" \
    CACHE_IMAGE_NAME='amdsiloai/pytorch-xdit-buildcache' \
    GITHUB_TOKEN='github-token' \
    DOCKERHUB_USERNAME='amd' \
    DOCKERHUB_TOKEN='docker-token' \
    GITHUB_REPOSITORY='AMD-AGI/diffusion-models-inference' \
    GRACE_PERIOD_DAYS=3 \
    DRY_RUN_SETTING="$dry_run" \
    MANUAL_DRY_RUN=inherit \
    GITHUB_STEP_SUMMARY="$summary_file" \
    MOCK_DELETE_LOG="${mock_dir}/deletes.log" \
    MOCK_ARG_LOG="${mock_dir}/args.log" \
    "$CLEANUP_SCRIPT"
}

: > "${mock_dir}/deletes.log"
: > "${mock_dir}/args.log"
dry_run_summary="${mock_dir}/dry-run-summary.md"
dry_run_output=$(run_cleanup true "$dry_run_summary" 2>&1)
assert_contains '  dry-run delete core-deleted' "$dry_run_output"
assert_contains '  dry-run delete core-custom' "$dry_run_output"
assert_not_contains '  dry-run delete core-main' "$dry_run_output"
assert_not_contains '  dry-run delete core-feature-live' "$dry_run_output"
assert_not_contains '  dry-run delete core-feature-foo' "$dry_run_output"
[[ ! -s "${mock_dir}/deletes.log" ]]
if grep -Fq 'docker-token' "${mock_dir}/args.log" ||
  grep -Fq 'test-token' "${mock_dir}/args.log"; then
  echo 'Credentials appeared in curl process arguments' >&2
  exit 1
fi

: > "${mock_dir}/deletes.log"
: > "${mock_dir}/args.log"
set +e
delete_output=$(run_cleanup false "${mock_dir}/delete-summary.md" 2>&1)
delete_status=$?
set -e
[[ "$delete_status" -ne 0 ]]
assert_contains '  deleted core-deleted' "$delete_output"
assert_contains '  deleted core-custom' "$delete_output"
assert_contains '::error::Failed to delete core-fail' "$delete_output"
assert_contains 'https://hub.docker.com/v2/namespaces/amdsiloai/repositories/pytorch-xdit-buildcache/tags/core-deleted' \
  "$(cat "${mock_dir}/deletes.log")"
assert_not_contains 'https://hub.docker.com/v2/namespaces/amdsiloai/repositories/pytorch-xdit-buildcache/tags/core-main' \
  "$(cat "${mock_dir}/deletes.log")"
assert_not_contains 'https://hub.docker.com/v2/namespaces/amdsiloai/repositories/pytorch-xdit-buildcache/tags/core-feature-foo' \
  "$(cat "${mock_dir}/deletes.log")"
if grep -Fq 'docker-token' "${mock_dir}/args.log" ||
  grep -Fq 'test-token' "${mock_dir}/args.log"; then
  echo 'Credentials appeared in curl process arguments' >&2
  exit 1
fi

: > "${mock_dir}/deletes.log"
: > "${mock_dir}/args.log"
set +e
fail_closed_output=$(
  MOCK_GH_FAILURE=true \
    PATH="${mock_dir}:${PATH}" \
    CACHE_IMAGE_NAME='amdsiloai/pytorch-xdit-buildcache' \
    GITHUB_TOKEN='github-token' \
    DOCKERHUB_USERNAME='amd' \
    DOCKERHUB_TOKEN='docker-token' \
    GITHUB_REPOSITORY='AMD-AGI/diffusion-models-inference' \
    GRACE_PERIOD_DAYS=3 \
    DRY_RUN_SETTING=false \
    MANUAL_DRY_RUN=inherit \
    MOCK_DELETE_LOG="${mock_dir}/deletes.log" \
    MOCK_ARG_LOG="${mock_dir}/args.log" \
    "$CLEANUP_SCRIPT" 2>&1
)
fail_closed_status=$?
set -e
[[ "$fail_closed_status" -ne 0 ]]
assert_contains '::error::GitHub branch discovery failed' "$fail_closed_output"
[[ ! -s "${mock_dir}/deletes.log" ]]

echo "cleanup-build-cache tests passed"
