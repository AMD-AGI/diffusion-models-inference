#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

required=(
    THEROCK_WHEEL_INDEX
    ROCM_GFX_TARGETS
    ROCM_WHEEL_VERSION
    PYTORCH_WHEEL_VERSION
    PYTORCH_VISION_WHEEL_VERSION
)
for name in "${required[@]}"; do
    if [[ -z "${!name:-}" ]]; then
        echo "${name} must be set" >&2
        exit 2
    fi
done

IFS=';' read -r -a targets <<< "${ROCM_GFX_TARGETS}"
extras=()
for target in "${targets[@]}"; do
    [[ -n "${target}" ]] || continue
    if [[ ! "${target}" =~ ^gfx[0-9a-z]+$ ]]; then
        echo "Invalid ROCM_GFX_TARGETS entry: ${target}" >&2
        exit 2
    fi
    extras+=("device-${target}")
done
if ((${#extras[@]} == 0)); then
    echo "ROCM_GFX_TARGETS did not contain a usable target" >&2
    exit 2
fi

device_extras="$(IFS=,; echo "${extras[*]}")"
packages=(
    "rocm[libraries,devel,profiler,${device_extras}]==${ROCM_WHEEL_VERSION}"
    "torch[${device_extras}]==${PYTORCH_WHEEL_VERSION}"
    "torchvision[${device_extras}]==${PYTORCH_VISION_WHEEL_VERSION}"
)
if [[ -n "${PYTORCH_AUDIO_WHEEL_VERSION:-}" ]]; then
    packages+=("torchaudio==${PYTORCH_AUDIO_WHEEL_VERSION}")
fi

python3 -m pip install --no-cache-dir --pre \
    --index-url "${THEROCK_WHEEL_INDEX}" \
    "${packages[@]}"

rocm-sdk init
python3 -m pip check

rocm_root="$(rocm-sdk path --root)"
test -d "${rocm_root}/include"
test -d "$(rocm-sdk path --cmake)"
test -x "$(rocm-sdk path --bin)/hipcc"

python3 - <<'PY'
import torch
import torchvision
import triton

print("torch", torch.__version__, "hip", torch.version.hip)
print("torchvision", torchvision.__version__)
print("triton", triton.__version__)
try:
    import torchaudio
except ModuleNotFoundError:
    pass
else:
    print("torchaudio", torchaudio.__version__)
PY
