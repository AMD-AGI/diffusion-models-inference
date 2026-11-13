#!/bin/bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -uo pipefail

WORKDIR=$(pwd)
ROOTDIR="${ROOTDIR:-/app/diffusion-models-inference}"
MIOPEN_USER_DB_PATH=${MIOPEN_USER_DB_PATH:-$ROOTDIR/data/miopen/userdb}
MIOPEN_FIND_MODE=${MIOPEN_FIND_MODE:-1}
MIOPEN_FIND_ENFORCE=${MIOPEN_FIND_ENFORCE:-3}
MIOPEN_TUNING_OUTPUT_DIR=${MIOPEN_TUNING_OUTPUT_DIR:-$WORKDIR/miopen-run}
FORCE_RETUNING=${FORCE_RETUNING:-false}

args=(
  --output-dir "$MIOPEN_TUNING_OUTPUT_DIR"
  --workloads "$ROOTDIR/data/miopen/workloads"
  --db-dir "$MIOPEN_USER_DB_PATH"
  --prefix-map "$ROOTDIR/data/miopen/prefixes.txt"
  --install-db
  --find-mode "$MIOPEN_FIND_MODE"
  --find-enforce "$MIOPEN_FIND_ENFORCE"
  --image "${MIOPEN_TUNING_IMAGE:-}"
  --image-digest "${MIOPEN_TUNING_IMAGE_DIGEST:-}"
)
if [ -n "${MIOPEN_DEBUG_CONV_DIRECT:-}" ]; then
  args+=(--debug-conv-direct "$MIOPEN_DEBUG_CONV_DIRECT")
fi
if [ "$FORCE_RETUNING" = "true" ]; then
  args+=(--force-retuning)
fi
if [ "${GITHUB_ACTIONS:-}" = "true" ]; then
  args+=(--github-annotations)
fi
if [ -n "${MIOPEN_TUNING_DEVICES:-}" ]; then
  args+=(--devices "$MIOPEN_TUNING_DEVICES")
fi

rm -f "$ROOTDIR/.tuning_successful"
(
  cd "$ROOTDIR" || exit 1
  PYTHONPATH="$ROOTDIR/src${PYTHONPATH:+:$PYTHONPATH}" python -m miopen_tuning run "${args[@]}"
)
status=$?

if [ "$status" -eq 0 ]; then
  touch "$ROOTDIR/.tuning_successful"
fi
exit "$status"
