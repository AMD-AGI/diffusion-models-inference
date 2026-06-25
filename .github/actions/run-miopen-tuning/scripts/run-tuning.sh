#!/usr/bin/env bash
set -euo pipefail

# Run MIOpen kernel tuning in a Docker container.
# Expects env vars: ARCH, FORCE_RETUNING, MIOPEN_FIND_MODE, MIOPEN_FIND_ENFORCE, DOCKER_IMAGE

rm -f .tuning_successful
docker run \
  --security-opt seccomp=unconfined \
  --device=/dev/kfd \
  --device=/dev/dri \
  --rm \
  --shm-size 128G \
  --name miopen-tune \
  --mount type=bind,src="$(pwd)",dst=/app/diffusion-models-inference \
  -e HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 \
  -e OMP_NUM_THREADS=16 \
  -e ARCH="$ARCH" \
  -e FORCE_RETUNING="$FORCE_RETUNING" \
  -e MIOPEN_USER_DB_PATH=/app/diffusion-models-inference/data/miopen/userdb \
  -e MIOPEN_FIND_MODE="$MIOPEN_FIND_MODE" \
  -e MIOPEN_FIND_ENFORCE="$MIOPEN_FIND_ENFORCE" \
  -e HOST_UID=$(id -g) \
  "$DOCKER_IMAGE" \
  bash -c "/app/diffusion-models-inference/data/miopen/tune.sh; amd-smi || rocm-smi || true"

if [ ! -f .tuning_successful ]; then
  exit 1
fi
