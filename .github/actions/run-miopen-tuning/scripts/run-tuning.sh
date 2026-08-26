#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

# Run MIOpen kernel tuning in a Docker container.
# Expects env vars: ARCH, FORCE_RETUNING, MIOPEN_FIND_MODE, MIOPEN_FIND_ENFORCE, DOCKER_IMAGE

rm -f .tuning_successful
RUNNER_WORK_ROOT=${RUNNER_WORK_ROOT:-/home/runner/_work}
[[ "$GITHUB_WORKSPACE" == "$RUNNER_WORK_ROOT/"* ]] ||
  { echo "GITHUB_WORKSPACE is outside $RUNNER_WORK_ROOT" >&2; exit 1; }

docker run \
  --security-opt seccomp=unconfined \
  --device=/dev/kfd \
  --device=/dev/dri \
  --rm \
  --shm-size 128G \
  --name miopen-tune \
  --mount type=bind,src="$RUNNER_WORK_ROOT",dst="$RUNNER_WORK_ROOT" \
  -e HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 \
  -e OMP_NUM_THREADS=16 \
  -e ARCH="$ARCH" \
  -e FORCE_RETUNING="$FORCE_RETUNING" \
  -e ROOTDIR="$GITHUB_WORKSPACE" \
  -e MIOPEN_USER_DB_PATH="$GITHUB_WORKSPACE/data/miopen/userdb" \
  -e MIOPEN_FIND_MODE="$MIOPEN_FIND_MODE" \
  -e MIOPEN_FIND_ENFORCE="$MIOPEN_FIND_ENFORCE" \
  -e HOST_UID="$(id -u)" \
  -e HOST_GID="$(id -g)" \
  -e GITHUB_WORKSPACE \
  "$DOCKER_IMAGE" \
  bash -c '
    bash "$ROOTDIR/data/miopen/tune.sh";
    (amd-smi || rocm-smi || true);
    source "$GITHUB_WORKSPACE/scripts/fix-workspace-permissions.sh";
  '

if [ ! -f .tuning_successful ]; then
  exit 1
fi
