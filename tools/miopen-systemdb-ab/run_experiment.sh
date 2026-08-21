#!/usr/bin/env bash
# Host-side wrapper to run the MIOpen system DB A/B experiment in Docker.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

DOCKER_IMAGE="${DOCKER_IMAGE:-amdsiloai/pytorch-xdit-staging:1cdf53a-temp}"
HIP_VISIBLE_DEVICES="${HIP_VISIBLE_DEVICES:-0}"
THRESHOLD_PCT="${THRESHOLD_PCT:-2.0}"
BENCHMARK_REPEATS="${BENCHMARK_REPEATS:-3}"
WORKLOADS_GLOB="${WORKLOADS_GLOB:-data/miopen/workloads/*.txt}"
RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)}"
OUTPUT_DIR="${OUTPUT_DIR:-${SCRIPT_DIR}/runs/${RUN_ID}}"

REPO_ROOT_ABS="$(cd "${REPO_ROOT}" && pwd)"
OUTPUT_DIR_ABS="$(mkdir -p "${OUTPUT_DIR}" && cd "${OUTPUT_DIR}" && pwd)"

if [[ "${OUTPUT_DIR_ABS}" != "${REPO_ROOT_ABS}"* ]]; then
  echo "ERROR: OUTPUT_DIR must be inside the repository so the Docker bind-mount persists results on the host." >&2
  echo "  Repository: ${REPO_ROOT_ABS}" >&2
  echo "  OUTPUT_DIR: ${OUTPUT_DIR_ABS}" >&2
  exit 1
fi

REL_OUTPUT="${OUTPUT_DIR_ABS#${REPO_ROOT_ABS}/}"
CONTAINER_OUTPUT="/app/diffusion-models-inference/${REL_OUTPUT}"

HOST_UID="${HOST_UID:-$(id -u)}"
HOST_GID="${HOST_GID:-$(id -g)}"

EXTRA_ARGS=()
if [[ "${SKIP_BENCHMARK_A:-false}" == "true" ]]; then
  EXTRA_ARGS+=(--skip-benchmark-a)
fi
if [[ "${SKIP_TUNE:-false}" == "true" ]]; then
  EXTRA_ARGS+=(--skip-tune)
fi
if [[ "${SKIP_BENCHMARK_B:-false}" == "true" ]]; then
  EXTRA_ARGS+=(--skip-benchmark-b)
fi
if [[ "${DRY_RUN:-false}" == "true" ]]; then
  EXTRA_ARGS+=(--dry-run)
fi

CONTAINER_REPO="/app/diffusion-models-inference"
CONTAINER_PYTHONPATH="${CONTAINER_REPO}/src:${CONTAINER_REPO}/tools/miopen-systemdb-ab"

mkdir -p "${OUTPUT_DIR_ABS}"

docker run \
  --cap-add=SYS_PTRACE \
  --security-opt seccomp=unconfined \
  --privileged \
  --device=/dev/kfd \
  --device=/dev/dri \
  --group-add video \
  --ipc=host \
  --network host \
  --user root \
  --shm-size 128G \
  --rm \
  --mount "type=bind,src=${REPO_ROOT_ABS},dst=${CONTAINER_REPO}" \
  -e HIP_VISIBLE_DEVICES="${HIP_VISIBLE_DEVICES}" \
  -e OMP_NUM_THREADS="${OMP_NUM_THREADS:-16}" \
  -e DOCKER_IMAGE="${DOCKER_IMAGE}" \
  -e HOST_UID="${HOST_UID}" \
  -e HOST_GID="${HOST_GID}" \
  -e PYTHONPATH="${CONTAINER_PYTHONPATH}" \
  "${DOCKER_IMAGE}" \
  bash -c "cd '${CONTAINER_REPO}' && python tools/miopen-systemdb-ab/run_experiment.py \
    --output-dir '${CONTAINER_OUTPUT}' \
    --workloads-glob '${WORKLOADS_GLOB}' \
    --threshold-pct '${THRESHOLD_PCT}' \
    --benchmark-repeats '${BENCHMARK_REPEATS}' \
    ${EXTRA_ARGS[*]}"

echo "Run artifacts on host: ${OUTPUT_DIR_ABS}"
echo "  report.md          -> ${OUTPUT_DIR_ABS}/report.md"
echo "  user DB (Arm A)    -> ${OUTPUT_DIR_ABS}/arm_a/user_db/"
echo "  user DB (Arm B)    -> ${OUTPUT_DIR_ABS}/arm_b/tuning_merged/"
echo "  artifact manifest  -> ${OUTPUT_DIR_ABS}/artifacts.json"
