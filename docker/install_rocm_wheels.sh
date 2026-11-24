#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
#
# Install ROCm and the PyTorch stack as Python wheels from one TheRock wheel
# index, then expose the ROCm SDK at /opt/rocm.
#
# torch pins rocm[libraries] and triton to the exact ROCm wheel version, so
# ROCM_WHEEL_VERSION selects one TheRock build for all packages. The nightly
# index also mirrors the pure-Python dependencies of torch, so this step uses
# only that index. A PyPI package cannot replace a ROCm build here.
#
# The torch and torchvision "device-<gfx>" extras pull the per-architecture
# kernel wheels (amd-torch-device-<gfx>, amd-torchvision-device-<gfx>, and
# rocm-sdk-device-<gfx>). Without them, torch has no device code.
#
# "rocm-sdk init" expands rocm-sdk-devel and hardlinks the device files into
# the devel tree. Run it in the same layer as the install: a hardlink to a file
# in a lower layer makes overlayfs copy the file. It runs here at build time,
# because the compiler trampolines otherwise start it on first use, for example
# in an AITER JIT build in a running container.

set -euo pipefail

if [[ $# -ne 0 ]]; then
    echo "usage: install_rocm_wheels" >&2
    exit 2
fi

readonly index="${ROCM_WHEEL_INDEX:?ROCM_WHEEL_INDEX must be set}"
readonly rocm_version="${ROCM_WHEEL_VERSION:?ROCM_WHEEL_VERSION must be set}"
readonly gfx_targets="${ROCM_GFX_TARGETS:?ROCM_GFX_TARGETS must be set}"
readonly torch_version="${TORCH_VERSION:?TORCH_VERSION must be set}"
readonly torchvision_version="${TORCHVISION_VERSION:?TORCHVISION_VERSION must be set}"
readonly torchaudio_version="${TORCHAUDIO_VERSION:?TORCHAUDIO_VERSION must be set}"
readonly constraints="${ROCM_WHEEL_CONSTRAINTS:?ROCM_WHEEL_CONSTRAINTS must be set}"
readonly rocm_link="/opt/rocm"
readonly local_version="+rocm${rocm_version}"

IFS=';' read -r -a targets <<< "${gfx_targets}"
usable=()
for target in "${targets[@]}"; do
    [[ -n "${target}" ]] || continue
    usable+=("${target}")
done
if [[ "${#usable[@]}" -eq 0 ]]; then
    echo "ROCM_GFX_TARGETS did not contain a usable target" >&2
    exit 2
fi
extras="$(printf 'device-%s,' "${usable[@]}")"
extras="${extras%,}"
readonly extras

pip install --no-cache-dir --index-url "${index}" \
    "torch[${extras}]==${torch_version}${local_version}" \
    "torchvision[${extras}]==${torchvision_version}${local_version}" \
    "torchaudio==${torchaudio_version}${local_version}" \
    "rocm-sdk-devel==${rocm_version}" \
    "rocm-profiler==${rocm_version}"

rocm-sdk init
sdk_root="$(rocm-sdk path --root)"
readonly sdk_root

# Tools and scripts (AITER fallbacks, MIOpen tuning, profiler scripts) expect
# the classic /opt/rocm root. A link keeps them working.
mkdir -p "$(dirname "${rocm_link}")"
ln -sfnT "${sdk_root}" "${rocm_link}"

echo "${rocm_link}/lib" > /etc/ld.so.conf.d/rocm.conf
if [[ -d "${rocm_link}/lib/rocm_sysdeps/lib" ]]; then
    echo "${rocm_link}/lib/rocm_sysdeps/lib" \
        > /etc/ld.so.conf.d/rocm_sysdeps.conf
fi
ldconfig

# Pins for all later pip runs (PIP_CONSTRAINT). A later dependency resolution
# must not replace the ROCm builds with PyPI builds.
pip freeze --exclude-editable |
    grep -iE '^(torch|torchvision|torchaudio|triton|rocm|rocm[-_][a-z0-9_-]+|amd[-_]torch[a-z0-9_-]*)==' \
    > "${constraints}"
echo "ROCm wheel constraints (${constraints}):"
cat "${constraints}"

test -x "${rocm_link}/bin/hipcc"
test -e "${rocm_link}/lib/libamdhip64.so"
test -d "${rocm_link}/lib/llvm/amdgcn/bitcode"
test -x "${rocm_link}/lib/llvm/bin/clang++"
test -x "${rocm_link}/lib/llvm/bin/ld.lld"
test -e "${rocm_link}/lib/cmake/hip-lang/hip-lang-config.cmake"
test -e "${rocm_link}/lib/libMIOpen.so"
test -e "${rocm_link}/lib/librccl.so"
test -e "${rocm_link}/lib/libhipblaslt.so"
test -e "${rocm_link}/lib/librocprofiler-sdk.so"
test -e "${rocm_link}/lib/libroctracer64.so"
test -d "${rocm_link}/share/amd_smi/amdsmi"
test -x "${rocm_link}/bin/MIOpenDriver"

ROCM_WHEEL_TARGETS="${usable[*]}" python3 - <<PY
import importlib.metadata as metadata
import os

import torch
import torchaudio
import torchvision
import triton

local = "${local_version}"
expected = {
    torch: "${torch_version}" + local,
    torchvision: "${torchvision_version}" + local,
    torchaudio: "${torchaudio_version}" + local,
}
for module, version in expected.items():
    if module.__version__ != version:
        raise SystemExit(f"{module.__name__} {module.__version__} != {version}")
if not torch.version.hip:
    raise SystemExit("torch is not a ROCm build")
for target in os.environ["ROCM_WHEEL_TARGETS"].split():
    for name in (f"amd-torch-device-{target}", f"rocm-sdk-device-{target}"):
        metadata.version(name)
print(f"torch {torch.__version__} (git {torch.version.git_version}), "
      f"HIP {torch.version.hip}, triton {triton.__version__}")
print(f"torchvision {torchvision.__version__}, torchaudio {torchaudio.__version__}")
PY
