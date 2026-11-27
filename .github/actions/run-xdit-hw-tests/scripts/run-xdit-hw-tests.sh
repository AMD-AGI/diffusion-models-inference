#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../../../scripts/container-name.sh"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../../../scripts/with-brr.sh"

case "${XDIT_TEST_SUITE:-}" in
  single) export BRR_GPU_REQUEST=1 ;;
  multi) export BRR_GPU_REQUEST=8 ;;
  *)
    echo "::error::XDIT_TEST_SUITE must be single or multi." >&2
    exit 1
    ;;
esac

RUNNER_WORK_ROOT=${RUNNER_WORK_ROOT:-/home/runner/_work}
for path in "$GITHUB_WORKSPACE" "$SCRIPT_DIR" "$WITH_BRR"; do
  [[ "$path" == "$RUNNER_WORK_ROOT/"* ]] ||
    { echo "::error::Test workspace and scripts must be inside $RUNNER_WORK_ROOT" >&2; exit 1; }
done

output_dir="$GITHUB_WORKSPACE/xdit-hw-tests/$ARCH"
mkdir -p "$output_dir"
rm -f "$output_dir/$XDIT_TEST_SUITE.xml"
container=$(container_name xdit-hw-tests)

prepare_brr_container

echo "Running $XDIT_TEST_SUITE GPU tests (requesting $BRR_GPU_REQUEST GPU(s))"
docker run \
  --security-opt seccomp=unconfined \
  --device=/dev/kfd \
  --device=/dev/dri \
  --rm --shm-size 128G \
  --name "$container" \
  --mount "type=bind,src=$RUNNER_WORK_ROOT,dst=$RUNNER_WORK_ROOT" \
  "${BRR_DOCKER_ARGS[@]}" \
  -e CUDA_VISIBLE_DEVICES \
  -e XDIT_TEST_SUITE \
  -e "XDIT_TEST_OUTPUT_DIR=$output_dir" \
  "$DOCKER_IMAGE" "${BRR_CONTAINER_LAUNCH[@]}" bash "$SCRIPT_DIR/run-in-container.sh"
