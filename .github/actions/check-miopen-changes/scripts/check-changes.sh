#!/usr/bin/env bash
set -euo pipefail

# Check if Dockerfile or MIOpen drivercmd files changed between two git refs.
# Expects env vars: BASE_REF, HEAD_REF
# Outputs: should_trigger (true/false) to GITHUB_OUTPUT

DOCKERFILE_CHANGED="false"
MIOPEN_CHANGED="false"
CHANGED_FILES=$(git diff --name-only "$BASE_REF" "$HEAD_REF")

if echo "$CHANGED_FILES" | grep -q "^docker/Dockerfile.ci$"; then
  DOCKERFILE_CHANGED="true"
  echo "Dockerfile changed"
fi

if echo "$CHANGED_FILES" | grep -q "^data/miopen/workloads/"; then
  MIOPEN_CHANGED="true"
  echo "MIOpen drivercmd files changed (workloads)"
fi

if [ "$DOCKERFILE_CHANGED" = "true" ] || [ "$MIOPEN_CHANGED" = "true" ]; then
  echo "should_trigger=true" >> "$GITHUB_OUTPUT"
  echo "MIOpen tuning changes detected, retuning will be triggered"
else
  echo "should_trigger=false" >> "$GITHUB_OUTPUT"
  echo "No MIOpen tuning changes detected"
fi
