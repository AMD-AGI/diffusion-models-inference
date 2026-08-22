#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

# Run xDiT benchmarks in a Docker container.
# Expects env vars: BENCHMARK_ONLY, RUN_PY_FLAGS, INPUT_BENCHMARK_FLAGS,
#   OUTPUT_DIR, ARCH, HF_CACHE_ARGS, HF_TOKEN, DOCKER_IMAGE,
#   COLLECT_HIPBLASLT_LOGS

MIOPEN_USER_DB_PATH_ENV=""
if [ "$BENCHMARK_ONLY" != "true" ]; then
  MIOPEN_USER_DB_PATH_ENV="-e MIOPEN_USER_DB_PATH=$GITHUB_WORKSPACE/data/miopen/userdb"
fi

EFFECTIVE_BENCHMARK_FLAGS="$RUN_PY_FLAGS $INPUT_BENCHMARK_FLAGS"
if [ "$COLLECT_HIPBLASLT_LOGS" = "true" ]; then
  EFFECTIVE_BENCHMARK_FLAGS="${EFFECTIVE_BENCHMARK_FLAGS} --collect-hipblaslt-logs"
fi

RUNNER_WORK_ROOT=${RUNNER_WORK_ROOT:-/home/runner/_work}
[[ "$GITHUB_WORKSPACE" == "$RUNNER_WORK_ROOT/"* ]] ||
  { echo "GITHUB_WORKSPACE is outside $RUNNER_WORK_ROOT" >&2; exit 1; }

docker run \
  --security-opt seccomp=unconfined \
  --device=/dev/kfd \
  --device=/dev/dri \
  --rm \
  --shm-size 128G \
  --name xdit-bench \
  --mount type=bind,src="$RUNNER_WORK_ROOT",dst="$RUNNER_WORK_ROOT" \
  $HF_CACHE_ARGS \
  $MIOPEN_USER_DB_PATH_ENV \
  -e CUDA_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 \
  -e OMP_NUM_THREADS=16 \
  -e HF_TOKEN="$HF_TOKEN" \
  -e BENCHMARK_FLAGS="${EFFECTIVE_BENCHMARK_FLAGS}" \
  -e BENCHMARK_OUTPUT_DIR="$GITHUB_WORKSPACE/$OUTPUT_DIR/$ARCH" \
  "$DOCKER_IMAGE" \
  bash -c '
    GFX_ARCH=$(rocminfo | grep -oP "gfx\d+" | head -1)
    FLAGS="$BENCHMARK_FLAGS"
    if [ -n "$GFX_ARCH" ] && [[ "$FLAGS" != *"--name"* ]]; then
      FLAGS="$FLAGS --tag $GFX_ARCH"
    fi
    python3 /app/.ci/run.py $FLAGS \
      --results-directory "$BENCHMARK_OUTPUT_DIR" \
      --csv-output-path "$BENCHMARK_OUTPUT_DIR/results.csv" \
      /app/.ci/benchmark_configs/*.yaml &&
      (amd-smi || rocm-smi || true)
  '