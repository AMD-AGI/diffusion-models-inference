#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

# Check whether a change qualifies for automatic MIOpen retuning.
# Expects env vars: BASE_REF, HEAD_REF
# Outputs: should_trigger (true/false), changed_paths to GITHUB_OUTPUT

# A change under either of these invalidates the tuned databases committed to the repo:
# the runtime itself, or the workloads that tune.sh feeds to MIOpenDriver.
# A trailing slash marks a directory prefix, anything else is an exact file path.
QUALIFYING_PATHS=(
  "docker/Dockerfile.ci"
  "data/miopen/workloads/"
)

CHANGED_FILES=$(git diff --name-only "$BASE_REF" "$HEAD_REF")

MATCHED=()
while IFS= read -r FILE; do
  [ -z "$FILE" ] && continue
  for PATTERN in "${QUALIFYING_PATHS[@]}"; do
    case "$PATTERN" in
      */) [[ "$FILE" == "$PATTERN"* ]] && MATCHED+=("$PATTERN") ;;
      *)  [[ "$FILE" == "$PATTERN" ]] && MATCHED+=("$PATTERN") ;;
    esac
  done
done <<< "$CHANGED_FILES"

if [ ${#MATCHED[@]} -gt 0 ]; then
  CHANGED_PATHS=$(printf '%s\n' "${MATCHED[@]}" | sort -u | paste -sd ' ' -)
  echo "Qualifying changes detected: ${CHANGED_PATHS}"
  {
    echo "should_trigger=true"
    echo "changed_paths=${CHANGED_PATHS}"
  } >> "$GITHUB_OUTPUT"
else
  echo "No MIOpen tuning changes detected"
  {
    echo "should_trigger=false"
    echo "changed_paths="
  } >> "$GITHUB_OUTPUT"
fi
