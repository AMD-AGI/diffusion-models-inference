#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/build-tune-finalise-rdna4.sh [BASE_IMAGE [FINAL_IMAGE]]

Build the RDNA4 candidate, run the tuning matrix on local gfx1201 GPUs, and
produce a final image containing the generated runtime tuning data.

Environment overrides:
  CONFIG       Config file or directory in the image (default: /app/.ci/benchmark_configs)
  STATE_DIR    Persistent host output/cache directory (default: .rdna4-finalise)
  HF_CACHE     Hugging Face cache directory (default: $HOME/.cache/huggingface)
  HF_TOKEN     Optional Hugging Face access token passed to the container
  SKIP_BUILD   Set to 1 to reuse BASE_IMAGE
  SKIP_RUN     Set to 1 to reuse tuning artifacts in STATE_DIR
EOF
}

if [[ ${1:-} == "-h" || ${1:-} == "--help" ]]; then usage; exit 0; fi

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BASE_IMAGE=${1:-diffusion-models-inference:rdna4-candidate}
FINAL_IMAGE=${2:-${BASE_IMAGE}-baked}
CONFIG=${CONFIG:-/app/.ci/benchmark_configs}
STATE_DIR=${STATE_DIR:-${ROOT}/.rdna4-finalise}
HF_CACHE=${HF_CACHE:-${HOME}/.cache/huggingface}

command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
[[ -e /dev/kfd && -d /dev/dri ]] || { echo "ROCm devices /dev/kfd and /dev/dri are required" >&2; exit 1; }

mkdir -p "${STATE_DIR}"/{home,outputs,miopen-db/cache,aiter-jit,aiter-tune,inductor-cache/triton,tunableop,merged,finalise-context} "${HF_CACHE}"

if [[ ${SKIP_BUILD:-0} != 1 ]]; then
    docker build --pull -f "${ROOT}/docker/Dockerfile.rdna4" -t "${BASE_IMAGE}" "${ROOT}"
fi

docker_args=(--rm --device=/dev/kfd --device=/dev/dri --ipc=host --security-opt=seccomp=unconfined)
for device in /dev/kfd /dev/dri/renderD*; do
    [[ -e ${device} ]] && docker_args+=(--group-add "$(stat -c '%g' "${device}")")
done
docker_args+=(
    --user "$(id -u):$(id -g)"
    -e HOME=/tmp/rdna4-home
    -e HF_HOME=/hf-cache
    -e MIOPEN_USER_DB_PATH=/miopen-db
    -e MIOPEN_CUSTOM_CACHE_DIR=/miopen-db/cache
    -e MIOPEN_FIND_MODE=NORMAL
    -e MIOPEN_FIND_ENFORCE=SEARCH
    -e AITER_JIT_DIR=/aiter-jit
    -e AITER_ONLINE_TUNE=1
    -e AITER_TUNE_DIR=/aiter-tune
    -e TORCHINDUCTOR_CACHE_DIR=/inductor-cache
    -e TRITON_CACHE_DIR=/inductor-cache/triton
    -e TORCHINDUCTOR_FX_GRAPH_CACHE=1
    -e TORCHINDUCTOR_AUTOGRAD_CACHE=1
    -e PYTORCH_TUNABLEOP_ENABLED=1
    -e PYTORCH_TUNABLEOP_TUNING=1
    -e PYTORCH_TUNABLEOP_FILENAME=/tunableop/tunableop_results.csv
    -e "BENCHMARK_CONFIG=${CONFIG}"
    -v "${STATE_DIR}/home:/tmp/rdna4-home"
    -v "${HF_CACHE}:/hf-cache"
    -v "${STATE_DIR}/outputs:/outputs"
    -v "${STATE_DIR}/miopen-db:/miopen-db"
    -v "${STATE_DIR}/aiter-jit:/aiter-jit"
    -v "${STATE_DIR}/aiter-tune:/aiter-tune"
    -v "${STATE_DIR}/inductor-cache:/inductor-cache"
    -v "${STATE_DIR}/tunableop:/tunableop"
)
[[ -n ${HF_TOKEN:-} ]] && docker_args+=(-e HF_TOKEN)

docker run "${docker_args[@]}" "${BASE_IMAGE}" python3 -c \
    "import torch; n=torch.cuda.device_count(); arches={torch.cuda.get_device_properties(i).gcnArchName.split(':')[0] for i in range(n)}; print(f'GPUs: {n}, arches: {sorted(arches)}'); assert n >= 4, 'the RDNA4 matrix requires at least four GPUs'; assert arches == {'gfx1201'}, f'expected only gfx1201 GPUs, got {arches}'"

if [[ ${SKIP_RUN:-0} != 1 ]]; then
    prepare_config=$(cat <<'PY'
import glob
import os
import re
import yaml

source = os.environ["BENCHMARK_CONFIG"]
paths = sorted(glob.glob(os.path.join(source, "*.yaml"))) if os.path.isdir(source) else [source]
workloads = []
for path in paths:
    with open(path) as stream:
        for workload in yaml.safe_load(stream) or []:
            if "rdna4" not in workload.get("tags", []):
                continue
            match = re.search(r"(?:^|[._])(\d+)gpu(?:[._]|$)", workload["name"])
            if not match:
                raise ValueError(f"cannot determine GPU count from {workload['name']}")
            workload["num_gpus"] = int(match.group(1))
            workloads.append(workload)
with open("/outputs/rdna4-runtime.yaml", "w") as stream:
    yaml.safe_dump(workloads, stream, sort_keys=False)
PY
    )
    docker run "${docker_args[@]}" "${BASE_IMAGE}" \
        python3 -c "${prepare_config}"
    docker run "${docker_args[@]}" "${BASE_IMAGE}" \
        python3 /app/.ci/run.py --no-clear-model-cache --tag rdna4 \
        --results-directory /outputs --csv-output-path /outputs/results.csv \
        --print-timing-summary /outputs/rdna4-runtime.yaml
fi

python3 "${ROOT}/scripts/merge_rdna4_tuning.py" \
    --tunable-dir "${STATE_DIR}/tunableop" \
    --aiter-dir "${STATE_DIR}/aiter-tune" \
    --output-dir "${STATE_DIR}/merged"

context=${STATE_DIR}/finalise-context
rm -rf "${context}"
mkdir -p "${context}/miopen-db"
: > "${context}/a8w8_blockscale_tuned_gemm_merged.csv"
: > "${context}/tunableop_results_merged.csv"

include_aiter=0
include_tunable=0
include_miopen=0
if [[ -s ${STATE_DIR}/merged/a8w8_blockscale_tuned_gemm_merged.csv ]]; then
    cp "${STATE_DIR}/merged/a8w8_blockscale_tuned_gemm_merged.csv" "${context}/"
    include_aiter=1
fi
if [[ -s ${STATE_DIR}/merged/tunableop_results_merged.csv ]]; then
    cp "${STATE_DIR}/merged/tunableop_results_merged.csv" "${context}/"
    include_tunable=1
fi
if find "${STATE_DIR}/miopen-db" -type f -print -quit | grep -q .; then
    cp -a "${STATE_DIR}/miopen-db/." "${context}/miopen-db/"
    include_miopen=1
fi
if (( include_aiter + include_tunable + include_miopen == 0 )); then
    echo "No tuning artifacts were produced; refusing to create an unchanged final image." >&2
    exit 1
fi

docker build -f "${ROOT}/docker/Dockerfile.rdna4.finalise" \
    --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
    --build-arg "INCLUDE_AITER=${include_aiter}" \
    --build-arg "INCLUDE_TUNABLE=${include_tunable}" \
    --build-arg "INCLUDE_MIOPEN=${include_miopen}" \
    -t "${FINAL_IMAGE}" "${context}"

echo "Built ${FINAL_IMAGE} from ${BASE_IMAGE}"
echo "Workload outputs and reusable tuning state: ${STATE_DIR}"
