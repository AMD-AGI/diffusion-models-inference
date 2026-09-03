#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

if [[ ${1:-} == "-h" || ${1:-} == "--help" ]]; then
    echo "Usage: scripts/rdna4/build.sh [BASE_IMAGE]"
    exit 0
fi

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
BASE_IMAGE=${1:-diffusion-models-inference:rdna4-candidate}

command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }

docker build --pull -f "${ROOT}/docker/Dockerfile.rdna4" -t "${BASE_IMAGE}" "${ROOT}"
echo "Built ${BASE_IMAGE}"
