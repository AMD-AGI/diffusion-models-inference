#!/usr/bin/env bash
set -euo pipefail

# Generate a GitHub Actions matrix JSON from workflow inputs.
# Expects env var: WORKFLOW_INPUTS (JSON string)

MI300_RUNNER=$(echo "$WORKFLOW_INPUTS" | jq -r '.mi300_runner // ""' | xargs)
MI325_RUNNER=$(echo "$WORKFLOW_INPUTS" | jq -r '.mi325_runner // ""' | xargs)
MI355_RUNNER=$(echo "$WORKFLOW_INPUTS" | jq -r '.mi355_runner // ""' | xargs)

TIMEOUT=1440  # 24h default timeout

MATRIX_JSON='{"include":['
FIRST=true

# MI300
if [ -n "$MI300_RUNNER" ]; then
  if [ "$FIRST" = "false" ]; then MATRIX_JSON="${MATRIX_JSON},"; fi
  MATRIX_JSON="${MATRIX_JSON}{\"arch\":\"mi300\",\"gfx_arch\":\"gfx942\",\"runner\":\"$MI300_RUNNER\",\"timeout\":${TIMEOUT}}"
  FIRST=false
fi

# MI325
if [ -n "$MI325_RUNNER" ]; then
  if [ "$FIRST" = "false" ]; then MATRIX_JSON="${MATRIX_JSON},"; fi
  MATRIX_JSON="${MATRIX_JSON}{\"arch\":\"mi325\",\"gfx_arch\":\"gfx942\",\"runner\":\"$MI325_RUNNER\",\"timeout\":${TIMEOUT}}"
  FIRST=false
fi

# MI355
if [ -n "$MI355_RUNNER" ]; then
  if [ "$FIRST" = "false" ]; then MATRIX_JSON="${MATRIX_JSON},"; fi
  MATRIX_JSON="${MATRIX_JSON}{\"arch\":\"mi355\",\"gfx_arch\":\"gfx950\",\"runner\":\"$MI355_RUNNER\",\"timeout\":${TIMEOUT}}"
  FIRST=false
fi

MATRIX_JSON="${MATRIX_JSON}]}"
echo "matrix=${MATRIX_JSON}" >> "$GITHUB_OUTPUT"
echo "Generated matrix: ${MATRIX_JSON}"
