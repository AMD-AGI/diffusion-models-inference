#!/usr/bin/env bash
# Launch the inference-testing driver container.
#
# The driver uses the runner's Docker daemon to manage the benchmark
# container (DockerServer) which runs the actual xDiT workloads.
#
# Inputs (env):
#   CONVERTED_DIR            — path to converted config directory
#   OUTPUT_DIR               — benchmark output directory name
#   ARCH                     — architecture name
#   HF_TOKEN                 — HuggingFace token
#   INFERENCE_TESTING_IMAGE  — inference-testing driver image
#   GITHUB_WORKSPACE         — workspace root
set -euo pipefail

mkdir -p "$GITHUB_WORKSPACE/$OUTPUT_DIR/$ARCH/.itt-driver"

docker run \
  --rm \
  --name inference-testing-driver \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --tmpfs /tmp:rw,noexec,nosuid,size=1g \
  --workdir /outputs/.itt-driver \
  -e HF_TOKEN="$HF_TOKEN" \
  -e CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0,1,2,3,4,5,6,7}" \
  -e DOCKER_HOST=tcp://host.docker.internal:2375 \
  --add-host host.docker.internal:host-gateway \
  --mount type=bind,src="$CONVERTED_DIR",dst=/configs,readonly \
  --mount type=bind,src="$GITHUB_WORKSPACE/$OUTPUT_DIR/$ARCH",dst=/outputs \
  --entrypoint inference-testing \
  "$INFERENCE_TESTING_IMAGE" \
  -c /configs
