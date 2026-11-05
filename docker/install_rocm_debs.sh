#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

if [[ $# -ne 0 ]]; then
    echo "usage: install_rocm_debs" >&2
    exit 2
fi

readonly package_repository="${ROCM_PACKAGE_REPOSITORY:?ROCM_PACKAGE_REPOSITORY must be set}"
readonly gfx_targets="${ROCM_GFX_TARGETS:?ROCM_GFX_TARGETS must be set}"
readonly rocm_root="${ROCM_HOME:-/opt/rocm}"

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

case "${package_repository}" in
    legacy)
        deb_series="$(configure_rocm_legacy_repo)"
        ;;
    native)
        deb_series="$(configure_rocm_native_repo)"
        ;;
    *)
        echo "Unknown ROCm package repository '${package_repository}'; expected legacy or native" >&2
        exit 2
        ;;
esac
readonly deb_series
readonly prefix="/opt/rocm/core-${deb_series}"

# Compiler, HIP runtime, and headers. runtime-dev depends on llvm-dev.
# Host libraries the AITER JIT link line names, plus the runtimes
# libtorch_hip loads. Per-arch shards hold device code. solver/sparse
# gfx packages are empty edges pulled in by the BLAS shard.
# rocSHMEM, hipFile, rocJPEG, rocDecode, and RPP stay out: this
# libtorch does not link them, and amdrocm-core would pull them in.
#
# developer-tools depends on the profiler, emulator, and debugger.
# It does not depend on amdrocm-core, so it does not pull the libraries
# left out above.
packages=(
    "amdrocm-runtime-dev${deb_series}"
    "amdrocm-ccl-dev${deb_series}"
    "amdrocm-blas-dev${deb_series}"
    "amdrocm-blas-host${deb_series}"
    "amdrocm-hipblas-common-dev${deb_series}"
    "amdrocm-solver-dev${deb_series}"
    "amdrocm-solver-host${deb_series}"
    "amdrocm-dnn-dev${deb_series}"
    "amdrocm-fft-dev${deb_series}"
    "amdrocm-rand-dev${deb_series}"
    "amdrocm-sparse-dev${deb_series}"
    "amdrocm-rccl-dev${deb_series}"
    "amdrocm-amdsmi${deb_series}"
    "amdrocm-developer-tools${deb_series}"
    "amdrocm-rdc${deb_series}"
    "amdrocm-opencl${deb_series}"
)
for target in "${usable[@]}"; do
    packages+=(
        "amdrocm-blas${deb_series}-${target}"
        "amdrocm-dnn${deb_series}-${target}"
        "amdrocm-rccl${deb_series}-${target}"
        "amdrocm-rand${deb_series}-${target}"
        "amdrocm-fft${deb_series}-${target}"
        "amdrocm-blas-test${deb_series}-${target}"
        "amdrocm-rccl-test${deb_series}-${target}"
    )
done

apt-get install -y --no-install-recommends "${packages[@]}"
rm -rf /var/lib/apt/lists/*

# Leaf packages install under /opt/rocm/core-<series>. The unversioned
# /opt/rocm/{bin,lib,include,...} links normally come from the amdrocm-core
# postinst, which this image does not install.
test -x "${prefix}/bin/hipcc"
test -e "${prefix}/lib/libamdhip64.so"
test -d "${prefix}/lib/llvm/amdgcn/bitcode"
major="${deb_series%%.*}"
score=100
update-alternatives --install /opt/rocm/core core "${prefix}" "${score}"
update-alternatives --install "/opt/rocm/core-${major}" "core-${major}" "${prefix}" "${score}"
update-alternatives --install /opt/rocm/bin rocm-bin "${prefix}/bin" "${score}"
update-alternatives --install /opt/rocm/lib rocm-lib "${prefix}/lib" "${score}"
update-alternatives --install /opt/rocm/include rocm-include "${prefix}/include" "${score}"
update-alternatives --install /opt/rocm/llvm rocm-llvm "${prefix}/llvm" "${score}"
update-alternatives --install /opt/rocm/amdgcn rocm-amdgcn "${prefix}/amdgcn" "${score}"
update-alternatives --install /opt/rocm/libexec rocm-libexec "${prefix}/libexec" "${score}"
update-alternatives --install /opt/rocm/share rocm-share "${prefix}/share" "${score}"

echo "${rocm_root}/lib" > /etc/ld.so.conf.d/rocm.conf
if [[ -d "${rocm_root}/lib/rocm_sysdeps/lib" ]]; then
    echo "${rocm_root}/lib/rocm_sysdeps/lib" \
        > /etc/ld.so.conf.d/rocm_sysdeps.conf
fi
ldconfig

# llvm-dev, a hard dependency of runtime-dev, is mostly static archives,
# including the Flang frontend. Those archives stay installed.
test -d "${rocm_root}/lib/llvm/amdgcn/bitcode"
test -e "${rocm_root}/lib/llvm/bin/clang++"
test -e "${rocm_root}/lib/libamdhip64.so"
test -d "${rocm_root}/lib/rocm_sysdeps/lib"
test -e "${rocm_root}/lib/librocprofiler-sdk.so"
test -e "${rocm_root}/lib/libroctracer64.so"
test -d "${rocm_root}/include/roctracer"
test -d "${rocm_root}/lib/rocm_sysdeps/include"
test -d "${rocm_root}/lib/rocm_sysdeps/lib/pkgconfig"
test -e "${rocm_root}/lib/libMIOpen.so"
test -e "${rocm_root}/lib/librccl.so"
test -e "${rocm_root}/lib/libhipblaslt.so"
test -e "${prefix}/lib/llvm/lib/libflangFrontend.a"
test -d "${rocm_root}/libexec/rocprofiler-compute"
