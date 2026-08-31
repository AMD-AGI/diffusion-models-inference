#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

readonly install_kind="${1:-}"
readonly release_id="${ROCM_RELEASE_ID:?ROCM_RELEASE_ID must be set}"
readonly deb_series="${ROCM_DEB_SERIES:?ROCM_DEB_SERIES must be set}"
readonly gfx_targets="${ROCM_GFX_TARGETS:?ROCM_GFX_TARGETS must be set}"
readonly rocm_root="${ROCM_HOME:-/opt/rocm}"
readonly repo_url="https://nightly.repo.amd.com/rocm/core/packages/deb/${release_id}"

if [[ "${install_kind}" != "runtime" && "${install_kind}" != "devel" ]]; then
    echo "Usage: $0 {runtime|devel}" >&2
    exit 2
fi

echo "deb [trusted=yes] ${repo_url} stable main" \
    > /etc/apt/sources.list.d/rocm-nightly.list

IFS=';' read -r -a targets <<< "${gfx_targets}"
if [[ "${#targets[@]}" -eq 0 ]]; then
    echo "ROCM_GFX_TARGETS must contain at least one target" >&2
    exit 2
fi

packages=()
case "${install_kind}" in
    runtime)
        for target in "${targets[@]}"; do
            [[ -n "${target}" ]] || continue
            packages+=("amdrocm-core${deb_series}-${target}")
        done
        ;;
    devel)
        packages+=(
            "amdrocm-developer-tools${deb_series}"
            "amdrocm-rdc${deb_series}"
            "amdrocm-opencl${deb_series}"
        )
        for target in "${targets[@]}"; do
            [[ -n "${target}" ]] || continue
            packages+=(
                "amdrocm-core-dev${deb_series}-${target}"
                "amdrocm-blas-test${deb_series}-${target}"
                "amdrocm-rccl-test${deb_series}-${target}"
            )
        done
        ;;
esac

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

# Nightly debs keep compiler binaries under lib/llvm. HIP/PyTorch still look
# for /opt/rocm/llvm/bin/clang++ (TheRock's historical layout).
if [[ -d "${rocm_root}/lib/llvm/bin" && -d "${rocm_root}/llvm/bin" ]]; then
    for bin in "${rocm_root}/lib/llvm/bin/"*; do
        [[ -e "${bin}" ]] || continue
        name="$(basename "${bin}")"
        dest="${rocm_root}/llvm/bin/${name}"
        if [[ ! -e "${dest}" ]]; then
            ln -s "${bin}" "${dest}"
        fi
    done
fi

if [[ "${install_kind}" == "runtime" ]]; then
    test -d "${rocm_root}/lib/llvm/amdgcn/bitcode"
    test -e "${rocm_root}/lib/llvm/bin/clang++"
    test -e "${rocm_root}/llvm/bin/clang++"
    test -e "${rocm_root}/lib/libamdhip64.so"
else
    test -d "${rocm_root}/include/roctracer"
    test -d "${rocm_root}/lib/rocm_sysdeps/include"
    test -d "${rocm_root}/lib/rocm_sysdeps/lib"
    test -d "${rocm_root}/lib/rocm_sysdeps/lib/pkgconfig"
    test -d "${rocm_root}/libexec/rocprofiler-compute"
    test -e "${rocm_root}/lib/librocprofiler-sdk.so"
    test -e "${rocm_root}/lib/libroctracer64.so"
    test -e "${rocm_root}/llvm/bin/clang++"
fi
