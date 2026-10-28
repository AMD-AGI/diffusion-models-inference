#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

# Run xDiT benchmarks in a Docker container.
# Expects env vars: BENCHMARK_ONLY, RUN_PY_FLAGS, INPUT_BENCHMARK_FLAGS,
#   OUTPUT_DIR, ARCH, HF_CACHE_ARGS, HF_TOKEN, DOCKER_IMAGE,
#   COLLECT_HIPBLASLT_LOGS, FORCE_DETERMINISM_CHECK,
#   DISABLE_BRR, WITH_BRR
#
# With brr the sweep is split into one container per experiment, run back to
# back, so a GPU allocation is held for a single experiment instead of for the
# whole sweep and other work can use the rest of the node meanwhile. Without
# brr the sweep runs as one container, exactly as it always has.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# The helper path is resolved relative to this action at runtime.
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../../../scripts/container-name.sh"

MIOPEN_USER_DB_PATH_ARGS=()
if [ "$BENCHMARK_ONLY" != "true" ]; then
  MIOPEN_USER_DB_PATH_ARGS=(
    -e "MIOPEN_USER_DB_PATH=$GITHUB_WORKSPACE/data/miopen/userdb"
  )
fi

DETERMINISM_ARGS=()
if [ "${FORCE_DETERMINISM_CHECK:-false}" = "true" ]; then
  DETERMINISM_ARGS=(-e CI_RUN_PY_FORCE_DETERMINISM_CHECK=1)
fi

HF_CACHE_DOCKER_ARGS=()
if [ -n "$HF_CACHE_ARGS" ]; then
  read -r -a HF_CACHE_DOCKER_ARGS <<< "$HF_CACHE_ARGS"
fi

EFFECTIVE_BENCHMARK_FLAGS="$RUN_PY_FLAGS $INPUT_BENCHMARK_FLAGS"
if [ "$COLLECT_HIPBLASLT_LOGS" = "true" ]; then
  EFFECTIVE_BENCHMARK_FLAGS="${EFFECTIVE_BENCHMARK_FLAGS} --collect-hipblaslt-logs"
fi

RUNNER_WORK_ROOT=${RUNNER_WORK_ROOT:-/home/runner/_work}
[[ "$GITHUB_WORKSPACE" == "$RUNNER_WORK_ROOT/"* ]] ||
  { echo "GITHUB_WORKSPACE is outside $RUNNER_WORK_ROOT" >&2; exit 1; }

BENCHMARK_OUTPUT_DIR="$GITHUB_WORKSPACE/$OUTPUT_DIR/$ARCH"
CONTAINER_NAME="$(container_name xdit-bench)"
echo "Running benchmarks as container '$CONTAINER_NAME'." >&2

# brr hands a job its GPUs by setting CUDA_VISIBLE_DEVICES on the process it
# starts, so docker has to forward the variable instead of pinning it; pinning
# it would hand the container GPUs it was never allocated. The default below is
# what this script has always used and keeps the no-brr path unchanged.
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0,1,2,3,4,5,6,7}"

DOCKER_ARGS=(
  --security-opt seccomp=unconfined
  --device=/dev/kfd
  --device=/dev/dri
  --rm
  --shm-size 128G
  --name "$CONTAINER_NAME"
  --mount "type=bind,src=$RUNNER_WORK_ROOT,dst=$RUNNER_WORK_ROOT"
  "${HF_CACHE_DOCKER_ARGS[@]}"
  "${MIOPEN_USER_DB_PATH_ARGS[@]}"
  "${DETERMINISM_ARGS[@]}"
  -e CUDA_VISIBLE_DEVICES
  -e OMP_NUM_THREADS=16
  -e HF_TOKEN="$HF_TOKEN"
  -e CI_RUN_PY_COLLECT_MIOPEN_DRIVER_COMMANDS=1
  -e BENCHMARK_OUTPUT_DIR="$BENCHMARK_OUTPUT_DIR"
  -e HOST_UID="$(id -u)"
  -e HOST_GID="$(id -g)"
  -e GITHUB_WORKSPACE
)

# The container-side snippets below are deliberately single-quoted: they are
# expanded by the shell inside the container, using the variables passed in
# with -e.
# shellcheck disable=SC2016
RESOLVE_GFX_TAG='
    GFX_ARCH=$(rocminfo | grep -oP "gfx\d+" | head -1)
    FLAGS="$BENCHMARK_FLAGS"
    if [ -n "$GFX_ARCH" ] && [[ "$FLAGS" != *"--name"* ]]; then
      FLAGS="$FLAGS --tag $GFX_ARCH"
    fi
'

# shellcheck disable=SC2016
RUN_BENCHMARKS='
    python3 /app/.ci/run.py $FLAGS \
      --results-directory "$BENCHMARK_OUTPUT_DIR" \
      --csv-output-path "$BENCHMARK_OUTPUT_DIR/results.csv" \
      /app/.ci/benchmark_configs/*.yaml;
    source "$GITHUB_WORKSPACE/scripts/fix-workspace-permissions.sh";
'

# shellcheck disable=SC2016
LIST_EXPERIMENTS='
    python3 /app/.ci/run.py $FLAGS --list-experiments /app/.ci/benchmark_configs/*.yaml
'

brr_is_usable() {
  [ "${DISABLE_BRR:-false}" != "true" ] && command -v brr >/dev/null 2>&1
}

if ! brr_is_usable; then
  # No brr: one container owns the node for the whole sweep.
  "$WITH_BRR" docker run "${DOCKER_ARGS[@]}" \
    -e BENCHMARK_FLAGS="$EFFECTIVE_BENCHMARK_FLAGS" \
    "$DOCKER_IMAGE" \
    bash -c "$RESOLVE_GFX_TAG$RUN_BENCHMARKS"
  exit 0
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required to split the sweep into per-experiment brr jobs" >&2
  exit 1
fi

# Resolve the experiment plan first. This container reads the GPU topology and
# the model cache but allocates no VRAM, so it deliberately runs outside brr.
echo "Resolving experiments to run..." >&2
PLAN=$(docker run "${DOCKER_ARGS[@]}" \
  -e BENCHMARK_FLAGS="$EFFECTIVE_BENCHMARK_FLAGS" \
  "$DOCKER_IMAGE" \
  bash -c "$RESOLVE_GFX_TAG$LIST_EXPERIMENTS")

mkdir -p "$BENCHMARK_OUTPUT_DIR"
printf '%s\n' "$PLAN" > "$BENCHMARK_OUTPUT_DIR/experiment_plan.json"

TOTAL=$(printf '%s' "$PLAN" | jq 'length')
if [ "$TOTAL" -eq 0 ]; then
  echo "No experiments matched the given filters." >&2
  exit 0
fi
echo "Resolved $TOTAL experiment(s); running each as its own brr job." >&2

# run.py's two model-cache flags are mutually exclusive and the plan already
# decided which one each experiment needs, so drop any inherited flag first.
BASE_FLAGS=${EFFECTIVE_BENCHMARK_FLAGS//--no-clear-model-cache/}
BASE_FLAGS=${BASE_FLAGS//--clear-model-cache/}

FAILED=0
INDEX=0
while IFS=$'\t' read -r NAME GPUS CLEAR_CACHE; do
  INDEX=$((INDEX + 1))

  EXPERIMENT_FLAGS="$BASE_FLAGS --name $NAME"
  if [ "$CLEAR_CACHE" = "true" ]; then
    EXPERIMENT_FLAGS="$EXPERIMENT_FLAGS --clear-model-cache"
  else
    EXPERIMENT_FLAGS="$EXPERIMENT_FLAGS --no-clear-model-cache"
  fi

  if [ -n "$GPUS" ]; then
    export BRR_GPU_REQUEST="$GPUS"
  else
    # No parallelism declared, so the experiment's footprint is unknown and
    # claiming the whole node is the only safe request.
    unset BRR_GPU_REQUEST
    echo "note: $NAME declares no parallelism; requesting the whole node" >&2
  fi

  echo "::group::Experiment $INDEX/$TOTAL: $NAME (${GPUS:-all} GPU(s))"
  STATUS=0
  "$WITH_BRR" docker run "${DOCKER_ARGS[@]}" \
    -e BENCHMARK_FLAGS="$EXPERIMENT_FLAGS" \
    "$DOCKER_IMAGE" \
    bash -c "$RESOLVE_GFX_TAG$RUN_BENCHMARKS" || STATUS=$?
  echo "::endgroup::"

  if [ "$STATUS" -ne 0 ]; then
    FAILED=1
    # brr rejects a request it can never satisfy (EXIT_REJECTED). Skipping that
    # experiment keeps one impossible request from blocking the rest.
    if [ "$STATUS" -eq 12 ]; then
      echo "::error::Experiment $NAME asked for ${GPUS:-all} GPU(s), which this node cannot provide; skipped."
    else
      echo "::error::Experiment $NAME failed with exit code $STATUS."
    fi
  fi
done < <(printf '%s' "$PLAN" | jq -r '.[] | [.name, (.num_gpus // "" | tostring), (.clear_model_cache | tostring)] | @tsv')

exit "$FAILED"
