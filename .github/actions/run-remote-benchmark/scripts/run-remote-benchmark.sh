#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

# Submit a benchmark job to the remote executor and report its progress.
# The pod joins as an ephemeral runner, so the benchmark's own output and
# artifacts reach GitHub through that job, not through here.
# Runs until the job finishes or the workflow is cancelled; cancelling also
# stops the cluster workload.
# Expects env vars: IMAGE_TAG, GPU_ARCH, RUN_ID, RUN_ATTEMPT
#   and optionally BENCHMARK_FLAGS, REQUEST_CPU, REQUEST_MEMORY, REQUEST_GPU

REQUESTS_DIR="/home/runner/kube-requests"
RESULTS_DIR="/home/runner/kube-results"
# The architecture is part of the key so concurrent runs against different
# hardware keep their requests, results and cluster workloads distinct. The
# attempt is included because RUN_ID is unchanged on a re-run, which would
# otherwise look like a request the executor had already handled.
RUN_KEY="${RUN_ID}-${RUN_ATTEMPT:-1}-${GPU_ARCH}"
RESULT_DIR="${RESULTS_DIR}/${RUN_KEY}"
POLL_INTERVAL=30
BENCHMARK_FLAGS="${BENCHMARK_FLAGS:-}"
REQUEST_CPU="${REQUEST_CPU:-default}"
REQUEST_MEMORY="${REQUEST_MEMORY:-default}"
REQUEST_GPU="${REQUEST_GPU:-default}"

if [ ! -d "$REQUESTS_DIR" ]; then
  echo "::error::This runner is not configured for remote execution"
  exit 1
fi

if ! [[ "$IMAGE_TAG" =~ ^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$ ]]; then
  echo "::error::Image tag is not a valid Docker tag: ${IMAGE_TAG}"
  exit 1
fi

# The executor re-validates these; this only fails the job early with a clear message.
if [ -n "$BENCHMARK_FLAGS" ]; then
  read -ra FLAG_TOKENS <<< "$BENCHMARK_FLAGS"
  if (( ${#FLAG_TOKENS[@]} % 2 != 0 )); then
    echo "::error::benchmark_flags must be --flag/value pairs"
    exit 1
  fi
  for (( i = 0; i < ${#FLAG_TOKENS[@]}; i += 2 )); do
    case "${FLAG_TOKENS[i]}" in
      --tag|--name) ;;
      *)
        echo "::error::Only --tag and --name are allowed, got: ${FLAG_TOKENS[i]}"
        exit 1
        ;;
    esac
    if ! [[ "${FLAG_TOKENS[i + 1]}" =~ ^[A-Za-z0-9._][A-Za-z0-9._-]{0,63}$ ]]; then
      echo "::error::Disallowed benchmark_flags value: ${FLAG_TOKENS[i + 1]}"
      exit 1
    fi
  done
fi

# The executor re-validates these against the same sets; this only fails the job
# early with a clear message. Kept in step with RESOURCE_CHOICES in the
# executor's workload_template.py.
require_choice() {
  local name="$1" value="$2"
  shift 2
  local option
  for option in "$@"; do
    if [ "$value" = "$option" ]; then
      return 0
    fi
  done
  echo "::error::${name} must be one of: $*"
  exit 1
}

RESOURCE_FIELDS=()
if [ "$REQUEST_CPU" != "default" ]; then
  require_choice cpu "$REQUEST_CPU" 16 32 64 128 256
  RESOURCE_FIELDS+=("\"cpu\":\"${REQUEST_CPU}\"")
fi
if [ "$REQUEST_MEMORY" != "default" ]; then
  require_choice memory "$REQUEST_MEMORY" 64Gi 128Gi 256Gi 512Gi 1024Gi 2048Gi
  RESOURCE_FIELDS+=("\"memory\":\"${REQUEST_MEMORY}\"")
fi
if [ "$REQUEST_GPU" != "default" ]; then
  require_choice gpu "$REQUEST_GPU" 1 2 4 8
  RESOURCE_FIELDS+=("\"gpu\":\"${REQUEST_GPU}\"")
fi

RESOURCES_JSON=""
RESOURCES_SUMMARY="architecture defaults"
if [ ${#RESOURCE_FIELDS[@]} -gt 0 ]; then
  RESOURCES_JSON=",\"resources\":{$(IFS=,; echo "${RESOURCE_FIELDS[*]}")}"
  RESOURCES_SUMMARY=$(IFS=' '; echo "${RESOURCE_FIELDS[*]}" | tr -d '"')
fi

# Mirrors the executor's own sanitising, so the name shown here is the one that
# ends up on the cluster and can be acted on before the job is even accepted.
if ! [[ "$RUN_KEY" =~ ^[a-z0-9]([a-z0-9-]{0,51}[a-z0-9])?$ ]]; then
  echo "::error::Run key is not usable as a workload name: ${RUN_KEY}"
  exit 1
fi
WORKLOAD_NAME="bench-${RUN_KEY}"
echo "Remote workload name: ${WORKLOAD_NAME}"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  {
    echo "### Remote benchmark: ${GPU_ARCH}"
    echo ""
    echo "Workload: \`${WORKLOAD_NAME}\`"
    echo ""
    echo "Resources: ${RESOURCES_SUMMARY}"
  } >> "$GITHUB_STEP_SUMMARY"
fi

echo "Submitting remote benchmark: image_tag=${IMAGE_TAG} arch=${GPU_ARCH} flags=${BENCHMARK_FLAGS:-<none>} resources=${RESOURCES_SUMMARY}"
cat > "${REQUESTS_DIR}/job-${RUN_KEY}.json" <<EOF
{"image_tag":"${IMAGE_TAG}","gpu_arch":"${GPU_ARCH}","run_id":"${RUN_KEY}","commit_sha":"${GITHUB_SHA}","benchmark_flags":"${BENCHMARK_FLAGS}"${RESOURCES_JSON}}
EOF

LINES_SEEN=0

# Cancelling the workflow only kills this step; without a marker the executor
# would keep the cluster workload running and holding GPUs to its own timeout.
on_cancel() {
  trap - INT TERM
  echo ""
  echo "Cancellation received; asking the executor to stop the remote workload."
  : > "${REQUESTS_DIR}/cancel-${RUN_KEY}"
  exit 130
}
trap on_cancel INT TERM

# Backgrounded so a signal interrupts the wait instead of being held until the
# sleep finishes, which would overrun the runner's grace period.
interruptible_sleep() {
  sleep "$1" &
  wait $! 2>/dev/null || true
}

# The executor appends cluster state here as it changes: phase transitions and
# why a job is still queued. The benchmark's own output does not come this way.
flush_output() {
  [ -f "${RESULT_DIR}/output.log" ] || return 0
  local total
  total=$(wc -l < "${RESULT_DIR}/output.log")
  (( total > LINES_SEEN )) || return 0

  tail -n +$(( LINES_SEEN + 1 )) "${RESULT_DIR}/output.log" | head -n $(( total - LINES_SEEN ))
  LINES_SEEN=$total
}

echo "Waiting for remote executor to pick up request..."
while true; do
  flush_output

  if [ -f "${RESULT_DIR}/status" ]; then
    STATUS=$(cat "${RESULT_DIR}/status")
    case "$STATUS" in
      completed|failed|cancelled)
        flush_output
        echo ""
        echo "Remote workload finished with status: ${STATUS}"
        exit "$(cat "${RESULT_DIR}/exit_code" 2>/dev/null || echo 1)"
        ;;
    esac
  fi

  interruptible_sleep "$POLL_INTERVAL"
done
