#!/usr/bin/env bash
set -euo pipefail

# Run xDiT benchmarks in a Docker container.
# Expects env vars: BENCHMARK_ONLY, RUN_PY_FLAGS, INPUT_BENCHMARK_FLAGS,
#   GFX_ARCH, OUTPUT_DIR, ARCH, HF_CACHE_ARGS, HF_TOKEN, DOCKER_IMAGE,
#   COLLECT_HIPBLASLT_LOGS

MIOPEN_USER_DB_PATH_ENV=""
if [ "$BENCHMARK_ONLY" != "true" ]; then
  MIOPEN_USER_DB_PATH_ENV="-e MIOPEN_USER_DB_PATH=/app/diffusion-models-inference/data/miopen/userdb"
fi

EFFECTIVE_BENCHMARK_FLAGS="$RUN_PY_FLAGS $INPUT_BENCHMARK_FLAGS"
if [ -n "$GFX_ARCH" ] && [[ "$EFFECTIVE_BENCHMARK_FLAGS" != *"--name"* ]]; then
  EFFECTIVE_BENCHMARK_FLAGS="${EFFECTIVE_BENCHMARK_FLAGS} --tag $GFX_ARCH"
fi
if [ "$COLLECT_HIPBLASLT_LOGS" = "true" ]; then
  EFFECTIVE_BENCHMARK_FLAGS="${EFFECTIVE_BENCHMARK_FLAGS} --collect-hipblaslt-logs"
fi

docker run \
  --security-opt seccomp=unconfined \
  --device=/dev/kfd \
  --device=/dev/dri \
  --rm \
  --shm-size 128G \
  --name xdit-bench \
  --mount type=bind,src="$GITHUB_WORKSPACE/$OUTPUT_DIR/$ARCH",dst=/outputs \
  --mount type=bind,src="$(pwd)",dst=/app/diffusion-models-inference \
  --mount type=bind,src="$GITHUB_WORKSPACE/references",dst=/app/references \
  $HF_CACHE_ARGS \
  $MIOPEN_USER_DB_PATH_ENV \
  -e CUDA_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 \
  -e OMP_NUM_THREADS=16 \
  -e HF_TOKEN="$HF_TOKEN" \
  -e BENCHMARK_FLAGS="${EFFECTIVE_BENCHMARK_FLAGS}" \
  "$DOCKER_IMAGE" \
  sh -c 'python3 /app/.ci/run.py $BENCHMARK_FLAGS /app/.ci/benchmark_configs/*.yaml && \
         python3 /app/.ci/quality_check.py --reference-path /app/references --benchmark-output-path /outputs; \
         amd-smi || rocm-smi || true'
