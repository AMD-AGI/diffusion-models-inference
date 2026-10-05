#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

readonly layer="${1:?usage: install_rocm_debs base strip-archives|keep-archives | devel}"
readonly archives="${2:-}"
readonly release_id="${ROCM_RELEASE_ID:?ROCM_RELEASE_ID must be set}"
readonly deb_series="${ROCM_DEB_SERIES:?ROCM_DEB_SERIES must be set}"
readonly gfx_targets="${ROCM_GFX_TARGETS:?ROCM_GFX_TARGETS must be set}"
readonly rocm_root="${ROCM_HOME:-/opt/rocm}"
readonly prefix="/opt/rocm/core-${deb_series}"
readonly repo_url="https://rocm.nightlies.amd.com/packages-multi-arch/deb/${release_id}"

case "${layer}" in
    base)
        case "${archives}" in
            strip-archives|keep-archives) ;;
            *)
                echo "base layer requires strip-archives or keep-archives" >&2
                exit 2
                ;;
        esac
        ;;
    devel)
        if [[ -n "${archives}" ]]; then
            echo "devel layer takes no archive mode" >&2
            exit 2
        fi
        ;;
    *)
        echo "unknown layer '${layer}'; expected base or devel" >&2
        exit 2
        ;;
esac

echo "deb [trusted=yes] ${repo_url} stable main" \
    > /etc/apt/sources.list.d/rocm-nightly.list

IFS=';' read -r -a targets <<< "${gfx_targets}"
if [[ "${#targets[@]}" -eq 0 ]]; then
    echo "ROCM_GFX_TARGETS must contain at least one target" >&2
    exit 2
fi

packages=()
case "${layer}" in
    base)
        # Compiler, HIP runtime, and headers. runtime-dev depends on llvm-dev.
        # Host libraries the AITER JIT link line names, plus the runtimes
        # libtorch_hip loads. Per-arch shards hold device code. solver/sparse
        # gfx packages are empty edges pulled in by the BLAS shard.
        # rocSHMEM, hipFile, rocJPEG, rocDecode, and RPP stay out: this
        # libtorch does not link them, and amdrocm-core would pull them in.
        packages+=(
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
        )
        for target in "${targets[@]}"; do
            [[ -n "${target}" ]] || continue
            packages+=(
                "amdrocm-blas${deb_series}-${target}"
                "amdrocm-dnn${deb_series}-${target}"
                "amdrocm-rccl${deb_series}-${target}"
                "amdrocm-rand${deb_series}-${target}"
                "amdrocm-fft${deb_series}-${target}"
            )
        done
        ;;
    devel)
        # developer-tools depends on the profiler, emulator, and debugger.
        # It does not depend on amdrocm-core, so this layer does not pull
        # the libraries rocm_base left out.
        packages=(
            "amdrocm-developer-tools${deb_series}"
            "amdrocm-rdc${deb_series}"
            "amdrocm-opencl${deb_series}"
        )
        for target in "${targets[@]}"; do
            [[ -n "${target}" ]] || continue
            packages+=(
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

# Leaf packages install under /opt/rocm/core-<series>. The unversioned
# /opt/rocm/{bin,lib,include,...} links normally come from the amdrocm-core
# postinst, which this image does not install.
if [[ "${layer}" == "base" ]]; then
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
fi

echo "${rocm_root}/lib" > /etc/ld.so.conf.d/rocm.conf
if [[ -d "${rocm_root}/lib/rocm_sysdeps/lib" ]]; then
    echo "${rocm_root}/lib/rocm_sysdeps/lib" \
        > /etc/ld.so.conf.d/rocm_sysdeps.conf
fi
ldconfig

# hipcc links libLLVM.so / libclang-cpp.so. llvm-dev is a hard dependency of
# runtime-dev and is mostly static archives, including the Flang frontend.
# AITER JIT and the torch link do not static-link LLVM. The host link still
# needs libclang_rt.builtins.a under lib/clang/, so that tree stays.
# strip-archives deletes in this same step as the install. A later layer
# would leave the archives in the image. keep-archives is the rocm_tree
# install that rocm_devel inherits. The package stays installed either way.
if [[ "${archives}" == "strip-archives" ]]; then
    find "${prefix}/lib/llvm/lib" -name '*.a' -not -path '*/lib/clang/*' -delete
fi

case "${layer}" in
    base)
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
        frontend="${prefix}/lib/llvm/lib/libflangFrontend.a"
        if [[ "${archives}" == "keep-archives" ]]; then
            test -e "${frontend}"
        else
            test ! -e "${frontend}"
        fi
        ;;
    devel)
        test -d "${rocm_root}/libexec/rocprofiler-compute"
        test -e "${prefix}/lib/llvm/lib/libflangFrontend.a"
        ;;
esac
