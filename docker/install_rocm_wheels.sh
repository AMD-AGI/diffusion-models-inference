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
#
# This script is the single source of the ROCm wheel requirement list. With
# --print-requirements, it writes that list to stdout and installs nothing.
# The nightly resolver (.github/actions/resolve-rocm-nightly) uses this mode to
# find the ROCm wheel builds that have all the pinned packages. Only
# ROCM_WHEEL_VERSION, ROCM_GFX_TARGETS, and the TORCH*_VERSION variables are
# necessary for this mode.
#
# After the install, ROCM_WHEEL_BUILD_INFO receives the build identity
# (TheRock manifest, torch source commit, HIP and Triton versions) as
# "key<TAB>value" lines. The record-image-versions action copies it.

set -euo pipefail

usage() {
    echo "usage: install_rocm_wheels [--print-requirements]" >&2
    exit 2
}

print_only=false
case "$#:${1:-}" in
    0:) ;;
    1:--print-requirements) print_only=true ;;
    *) usage ;;
esac

readonly rocm_version="${ROCM_WHEEL_VERSION:?ROCM_WHEEL_VERSION must be set}"
readonly gfx_targets="${ROCM_GFX_TARGETS:?ROCM_GFX_TARGETS must be set}"
readonly torch_version="${TORCH_VERSION:?TORCH_VERSION must be set}"
readonly torchvision_version="${TORCHVISION_VERSION:?TORCHVISION_VERSION must be set}"
readonly torchaudio_version="${TORCHAUDIO_VERSION:?TORCHAUDIO_VERSION must be set}"
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

# All wheels from one TheRock build. The device wheels and the SDK parts also
# come through the torch dependencies; the explicit pins make the list
# complete for the resolver and make pip fail if torch pins other versions.
# torch also pins triton, but its version (for example
# 3.9.0+git0a6e13e6.rocm10.3.0a20261108) is not known before the install. The
# resolver checks for a triton build with the same "rocm" suffix.
requirements=(
    "torch[${extras}]==${torch_version}${local_version}"
    "torchvision[${extras}]==${torchvision_version}${local_version}"
    "torchaudio==${torchaudio_version}${local_version}"
    "rocm-sdk-core==${rocm_version}"
    "rocm-sdk-libraries==${rocm_version}"
    "rocm-sdk-devel==${rocm_version}"
    "rocm-profiler==${rocm_version}"
)
for target in "${usable[@]}"; do
    requirements+=(
        "amd-torch-device-${target}==${torch_version}${local_version}"
        "amd-torchvision-device-${target}==${torchvision_version}${local_version}"
        "rocm-sdk-device-${target}==${rocm_version}"
    )
done
readonly requirements

if [[ "${print_only}" == true ]]; then
    printf '%s\n' "${requirements[@]}"
    exit 0
fi

readonly index="${ROCM_WHEEL_INDEX:?ROCM_WHEEL_INDEX must be set}"
readonly constraints="${ROCM_WHEEL_CONSTRAINTS:?ROCM_WHEEL_CONSTRAINTS must be set}"
readonly build_info="${ROCM_WHEEL_BUILD_INFO:?ROCM_WHEEL_BUILD_INFO must be set}"
readonly rocm_link="/opt/rocm"

pip install --no-cache-dir --index-url "${index}" "${requirements[@]}"

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

ROCM_WHEEL_TARGETS="${usable[*]}" ROCM_WHEEL_INDEX_URL="${index}" \
    ROCM_WHEEL_BUILD_INFO_PATH="${build_info}" python3 - <<PY
import importlib.metadata as metadata
import json
import os
from pathlib import Path

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

core = metadata.distribution("rocm-sdk-core")
manifest = json.loads(
    Path(core.locate_file("_rocm_sdk_core/share/therock/therock_manifest.json")).read_text()
)
info = {
    "rocm_wheel_index": os.environ["ROCM_WHEEL_INDEX_URL"],
    "rocm_wheel_version": "${rocm_version}",
    "rocm_gfx_targets": " ".join(os.environ["ROCM_WHEEL_TARGETS"].split()),
    "therock_manifest.rocm_package_version": manifest.get("rocm_package_version", ""),
    "therock_manifest.the_rock_commit": manifest.get("the_rock_commit", ""),
    "therock_manifest.github_run_id": manifest.get("github_run_id", ""),
    "torch.version.git_version": torch.version.git_version,
    "torch.version.hip": torch.version.hip,
    "triton": metadata.version("triton"),
}
if info["therock_manifest.rocm_package_version"] != "${rocm_version}":
    raise SystemExit(f"TheRock manifest version {info['therock_manifest.rocm_package_version']} != ${rocm_version}")
Path(os.environ["ROCM_WHEEL_BUILD_INFO_PATH"]).write_text(
    "".join(f"{key}\t{value}\n" for key, value in info.items())
)

print(f"torch {torch.__version__} (git {torch.version.git_version}), "
      f"HIP {torch.version.hip}, triton {triton.__version__}")
print(f"torchvision {torchvision.__version__}, torchaudio {torchaudio.__version__}")
PY
echo "ROCm wheel build info (${build_info}):"
cat "${build_info}"
