#!/usr/bin/env bash
set -euo pipefail

# Generate a GitHub Actions matrix JSON from workflow inputs.
# Expects env var: WORKFLOW_INPUTS (JSON string with .gpu_runners comma-separated list)

GPU_RUNNERS=$(echo "$WORKFLOW_INPUTS" | jq -r '.gpu_runners // ""' | xargs)

if [ -z "$GPU_RUNNERS" ]; then
  echo "Error: gpu_runners is empty"
  exit 1
fi

TIMEOUT=1440  # 24h default timeout

MATRIX_JSON='{"include":['
FIRST=true

IFS=',' read -ra TAGS <<< "$GPU_RUNNERS"
for TAG in "${TAGS[@]}"; do
  TAG=$(echo "$TAG" | xargs)
  [ -z "$TAG" ] && continue

  if [ "$FIRST" = "false" ]; then MATRIX_JSON="${MATRIX_JSON},"; fi
  MATRIX_JSON="${MATRIX_JSON}{\"arch\":\"${TAG}\",\"runner\":\"${TAG}\",\"timeout\":${TIMEOUT}}"
  FIRST=false
done

MATRIX_JSON="${MATRIX_JSON}]}"
echo "matrix=${MATRIX_JSON}" >> "$GITHUB_OUTPUT"
echo "Generated matrix: ${MATRIX_JSON}"
