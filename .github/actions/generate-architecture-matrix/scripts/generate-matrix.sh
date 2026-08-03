#!/usr/bin/env bash
set -euo pipefail

# Generate a GitHub Actions matrix JSON from workflow inputs.
# Expects env var: WORKFLOW_INPUTS (JSON string)

GFX942_RUNNER=$(echo "$WORKFLOW_INPUTS" | jq -r '.gfx942_runner // ""' | xargs)
GFX950_RUNNER=$(echo "$WORKFLOW_INPUTS" | jq -r '.gfx950_runner // ""' | xargs)

TIMEOUT=1440  # 24h default timeout

MATRIX_JSON='{"include":['
FIRST=true

# gfx942
if [ -n "$GFX942_RUNNER" ]; then
  if [ "$FIRST" = "false" ]; then MATRIX_JSON="${MATRIX_JSON},"; fi
  MATRIX_JSON="${MATRIX_JSON}{\"arch\":\"gfx942\",\"gfx_arch\":\"gfx942\",\"runner\":\"$GFX942_RUNNER\",\"timeout\":${TIMEOUT}}"
  FIRST=false
fi

# gfx950
if [ -n "$GFX950_RUNNER" ]; then
  if [ "$FIRST" = "false" ]; then MATRIX_JSON="${MATRIX_JSON},"; fi
  MATRIX_JSON="${MATRIX_JSON}{\"arch\":\"gfx950\",\"gfx_arch\":\"gfx950\",\"runner\":\"$GFX950_RUNNER\",\"timeout\":${TIMEOUT}}"
  FIRST=false
fi

MATRIX_JSON="${MATRIX_JSON}]}"
echo "matrix=${MATRIX_JSON}" >> "$GITHUB_OUTPUT"
echo "Generated matrix: ${MATRIX_JSON}"
