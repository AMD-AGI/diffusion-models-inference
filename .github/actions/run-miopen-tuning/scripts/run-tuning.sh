#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

# Run MIOpen kernel tuning in a Docker container.
# Expects env vars: ARCH, FORCE_RETUNING, DOCKER_IMAGE,
#   DISABLE_BRR, WITH_BRR

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# The helper path is resolved relative to this action at runtime.
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../../../scripts/container-name.sh"

rm -f .tuning_successful
RUNNER_WORK_ROOT=${RUNNER_WORK_ROOT:-/home/runner/_work}
[[ "$GITHUB_WORKSPACE" == "$RUNNER_WORK_ROOT/"* ]] ||
  { echo "GITHUB_WORKSPACE is outside $RUNNER_WORK_ROOT" >&2; exit 1; }

CONTAINER_NAME="$(container_name miopen-tune)"
echo "Running MIOpen tuning as container '$CONTAINER_NAME'." >&2

# The bash -c body below is deliberately single-quoted: it is expanded by the
# shell inside the container, using the variables passed in with -e.
# shellcheck disable=SC2016
"$WITH_BRR" docker run \
  --security-opt seccomp=unconfined \
  --device=/dev/kfd \
  --device=/dev/dri \
  --rm \
  --shm-size 128G \
  --name "$CONTAINER_NAME" \
  --mount type=bind,src="$RUNNER_WORK_ROOT",dst="$RUNNER_WORK_ROOT" \
  -e HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 \
  -e OMP_NUM_THREADS=16 \
  -e ARCH="$ARCH" \
  -e FORCE_RETUNING="$FORCE_RETUNING" \
  -e ROOTDIR="$GITHUB_WORKSPACE" \
  -e MIOPEN_USER_DB_PATH="$GITHUB_WORKSPACE/data/miopen/userdb" \
  -e MIOPEN_FIND_MODE="1" \
  -e MIOPEN_FIND_ENFORCE="3" \
  -e HOST_UID="$(id -u)" \
  -e HOST_GID="$(id -g)" \
  -e GITHUB_WORKSPACE \
  "$DOCKER_IMAGE" \
  bash -c '
    bash "$ROOTDIR/data/miopen/tune.sh";
    source "$GITHUB_WORKSPACE/scripts/fix-workspace-permissions.sh";
  '

if [ ! -f .tuning_successful ]; then
  exit 1
fi
