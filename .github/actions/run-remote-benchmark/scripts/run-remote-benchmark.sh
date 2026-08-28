#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

# Submit a benchmark job to the remote executor and stream its output back.
# Expects env vars: IMAGE_TAG, GPU_ARCH, OUTPUT_PATH, TIMEOUT_MINUTES, RUN_ID
#   and optionally BENCHMARK_FLAGS

REQUESTS_DIR="/home/runner/kube-requests"
RESULTS_DIR="/home/runner/kube-results"
RESULT_DIR="${RESULTS_DIR}/${RUN_ID}"
POLL_INTERVAL=30
BENCHMARK_FLAGS="${BENCHMARK_FLAGS:-}"

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

mkdir -p "$OUTPUT_PATH"

echo "Submitting remote benchmark: image_tag=${IMAGE_TAG} arch=${GPU_ARCH} flags=${BENCHMARK_FLAGS:-<none>}"
cat > "${REQUESTS_DIR}/job-${RUN_ID}.json" <<EOF
{"image_tag":"${IMAGE_TAG}","gpu_arch":"${GPU_ARCH}","run_id":"${RUN_ID}","commit_sha":"${GITHUB_SHA}","benchmark_flags":"${BENCHMARK_FLAGS}"}
EOF

DEADLINE=$(( $(date +%s) + TIMEOUT_MINUTES * 60 ))
LINES_SEEN=0
IN_ARCHIVE=0

# The results archive arrives as one very long base64 line; echoing it would
# bury the benchmark output, so it is held back and unpacked at the end.
flush_output() {
  [ -f "${RESULT_DIR}/output.log" ] || return 0
  local total line
  total=$(wc -l < "${RESULT_DIR}/output.log")
  (( total > LINES_SEEN )) || return 0

  while IFS= read -r line; do
    case "$line" in
      "===ARTIFACT_TGZ_BEGIN===")
        IN_ARCHIVE=1
        echo "(receiving results archive...)"
        continue
        ;;
      "===ARTIFACT_TGZ_END===")
        IN_ARCHIVE=0
        continue
        ;;
    esac
    (( IN_ARCHIVE )) || printf '%s\n' "$line"
  done < <(tail -n +$(( LINES_SEEN + 1 )) "${RESULT_DIR}/output.log" | head -n $(( total - LINES_SEEN )))

  LINES_SEEN=$total
}

collect_results() {
  [ -f "${RESULT_DIR}/output.log" ] || return 0

  # Keep the saved log readable by dropping the base64 blob from it.
  awk '/^===ARTIFACT_TGZ_BEGIN===$/{s=1} /^===ARTIFACT_TGZ_END===$/{s=0;next} !s' \
    "${RESULT_DIR}/output.log" > "${OUTPUT_PATH}/output.log"

  # The results volume is read-only here, so stage the blob in scratch space.
  local b64
  b64="$(mktemp "${RUNNER_TEMP:-/tmp}/remote-archive.XXXXXX")"
  awk '/^===ARTIFACT_TGZ_BEGIN===$/{f=1;next} /^===ARTIFACT_TGZ_END===$/{f=0} f' \
    "${RESULT_DIR}/output.log" > "$b64"
  if [ -s "$b64" ]; then
    if base64 -d < "$b64" | tar xzf - -C "$OUTPUT_PATH"; then
      echo "Unpacked results archive into $(basename "$OUTPUT_PATH")/"
    else
      echo "::warning::Could not unpack the results archive"
    fi
  fi
  rm -f "$b64"

  # Fall back to the plain-text CSV if the archive was missing or unusable.
  if [ ! -f "${OUTPUT_PATH}/results.csv" ]; then
    awk '/^===RESULTS_CSV_BEGIN===$/{f=1;next} /^===RESULTS_CSV_END===$/{f=0} f' \
      "${RESULT_DIR}/output.log" > "${OUTPUT_PATH}/results.csv"
    [ -s "${OUTPUT_PATH}/results.csv" ] || rm -f "${OUTPUT_PATH}/results.csv"
  fi
}

echo "Waiting for remote executor to pick up request..."
while true; do
  if (( $(date +%s) >= DEADLINE )); then
    echo "::error::Timed out after ${TIMEOUT_MINUTES} minutes waiting for the remote benchmark"
    exit 1
  fi

  flush_output

  if [ -f "${RESULT_DIR}/status" ]; then
    STATUS=$(cat "${RESULT_DIR}/status")
    case "$STATUS" in
      completed|failed)
        flush_output
        collect_results
        echo ""
        echo "Remote benchmark finished with status: ${STATUS}"
        exit "$(cat "${RESULT_DIR}/exit_code" 2>/dev/null || echo 1)"
        ;;
    esac
  fi

  sleep "$POLL_INTERVAL"
done
