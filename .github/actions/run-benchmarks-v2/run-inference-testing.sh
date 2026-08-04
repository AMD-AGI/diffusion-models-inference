#!/usr/bin/env bash
# Launch the inference-testing driver container.
#
# The driver connects to the DinD daemon and manages the benchmark
# container (DockerServer) which runs the actual xDiT workloads.
#
# Inputs (env):
#   CONVERTED_DIR            — path to converted config directory
#   OUTPUT_DIR               — benchmark output directory name
#   ARCH                     — architecture name
#   HF_CACHE_ARGS            — Docker CLI args for HF cache mount
#   HF_TOKEN                 — HuggingFace token
#   INFERENCE_TESTING_IMAGE  — inference-testing driver image
#   GITHUB_WORKSPACE         — workspace root
#   DOCKER_HOST              — Docker daemon endpoint (optional, defaults to tcp://dind:2375)
set -euo pipefail

docker run \
  --rm \
  --name inference-testing-driver \
  -e DOCKER_HOST="${DOCKER_HOST:-tcp://dind:2375}" \
  -e HF_TOKEN="$HF_TOKEN" \
  -e CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0,1,2,3,4,5,6,7}" \
  --mount type=bind,src="$CONVERTED_DIR",dst=/configs \
  --mount type=bind,src="$GITHUB_WORKSPACE/$OUTPUT_DIR/$ARCH",dst=/outputs \
  --mount type=bind,src="$(pwd)",dst=/app/diffusion-models-inference-private \
  ${HF_CACHE_ARGS:-} \
  "$INFERENCE_TESTING_IMAGE" \
  inference-testing -c /configs
