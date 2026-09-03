#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/rdna4/finalise.sh [BASE_IMAGE [FINAL_IMAGE]]

Merge generated tuning data into a final RDNA4 image.

Environment overrides:
  STATE_DIR    Persistent host output/cache directory (default: $HOME/rdna4-finalise)
EOF
}

if [[ ${1:-} == "-h" || ${1:-} == "--help" ]]; then usage; exit 0; fi

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
BASE_IMAGE=${1:-diffusion-models-inference:rdna4-candidate}
FINAL_IMAGE=${2:-${BASE_IMAGE}-tuned}
STATE_DIR=${STATE_DIR:-${HOME}/rdna4-finalise}

command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }

mkdir -p "${STATE_DIR}"/{miopen-db,aiter-tune,tunableop,merged,finalise-context}

python3 "${ROOT}/scripts/rdna4/merge_rdna4_tuning.py" \
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
cp "${ROOT}/scripts/rdna4/runtime-entrypoint.sh" "${context}/runtime-entrypoint.sh"
if (( include_aiter + include_tunable + include_miopen == 0 )); then
    echo "No tuning artifacts were produced; refusing to create an unchanged final image." >&2
    exit 1
fi

docker build -f "${ROOT}/scripts/rdna4/Dockerfile.rdna4.finalise" \
    --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
    --build-arg "INCLUDE_AITER=${include_aiter}" \
    --build-arg "INCLUDE_TUNABLE=${include_tunable}" \
    --build-arg "INCLUDE_MIOPEN=${include_miopen}" \
    -t "${FINAL_IMAGE}" "${context}"

echo "Built ${FINAL_IMAGE} from ${BASE_IMAGE}"
echo "Reusable tuning state: ${STATE_DIR}"
