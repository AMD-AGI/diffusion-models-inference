#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

readonly release_id="${ROCM_RELEASE_ID:?ROCM_RELEASE_ID must be set}"
readonly deb_series="${ROCM_DEB_SERIES:?ROCM_DEB_SERIES must be set}"
readonly gfx_targets="${ROCM_GFX_TARGETS:?ROCM_GFX_TARGETS must be set}"
readonly rocm_root="${ROCM_HOME:-/opt/rocm}"
readonly repo_url="https://nightly.repo.amd.com/rocm/core/packages/deb/${release_id}"

echo "deb [trusted=yes] ${repo_url} stable main" \
    > /etc/apt/sources.list.d/rocm-nightly.list

IFS=';' read -r -a targets <<< "${gfx_targets}"
if [[ "${#targets[@]}" -eq 0 ]]; then
    echo "ROCM_GFX_TARGETS must contain at least one target" >&2
    exit 2
fi

packages=(
    "amdrocm-developer-tools${deb_series}"
    "amdrocm-rdc${deb_series}"
    "amdrocm-opencl${deb_series}"
)
for target in "${targets[@]}"; do
    [[ -n "${target}" ]] || continue
    packages+=(
        "amdrocm-core${deb_series}-${target}"
        "amdrocm-core-dev${deb_series}-${target}"
        "amdrocm-blas-test${deb_series}-${target}"
        "amdrocm-rccl-test${deb_series}-${target}"
    )
done

if [[ "${#packages[@]}" -eq 0 ]]; then
    echo "ROCM_GFX_TARGETS did not contain a usable target" >&2
    exit 2
fi

apt-get update -qq
apt-get install -y --no-install-recommends "${packages[@]}"
rm -rf /var/lib/apt/lists/*

echo "${rocm_root}/lib" > /etc/ld.so.conf.d/rocm.conf
if [[ -d "${rocm_root}/lib/rocm_sysdeps/lib" ]]; then
    echo "${rocm_root}/lib/rocm_sysdeps/lib" \
        > /etc/ld.so.conf.d/rocm_sysdeps.conf
fi
ldconfig

test -d "${rocm_root}/lib/llvm/amdgcn/bitcode"
test -e "${rocm_root}/lib/llvm/bin/clang++"
test -e "${rocm_root}/lib/libamdhip64.so"
test -d "${rocm_root}/include/roctracer"
test -d "${rocm_root}/lib/rocm_sysdeps/include"
test -d "${rocm_root}/lib/rocm_sysdeps/lib"
test -d "${rocm_root}/lib/rocm_sysdeps/lib/pkgconfig"
test -d "${rocm_root}/libexec/rocprofiler-compute"
test -e "${rocm_root}/lib/librocprofiler-sdk.so"
test -e "${rocm_root}/lib/libroctracer64.so"
