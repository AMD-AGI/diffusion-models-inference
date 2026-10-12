#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
#
# Fill $ROCM_PATH/llvm/bin so FlyDSL/MLIR can find ld.lld.
#
# Nightly debs install the LLVM toolchain at ${ROCM_PATH}/lib/llvm/bin and leave
# ${ROCM_PATH}/llvm/bin as a stub of clang .cfg files. FlyDSL's bundled MLIR
# still serializes GPU binaries by invoking $ROCM_PATH/llvm/bin/ld.lld (classic
# toolkit layout), which fails with "lld invocation failed" when that path is
# missing. PATH lookups are a second, weaker contract; docker/Dockerfile.ci
# puts lib/llvm/bin on PATH via ENV and this script does not try to persist that.
#
# The fix is a symlink per missing name, never a copy, into the stub directory.
# Existing cfg files (and anything else already present) are left alone.
#
# This shim is expected to expire in either of two ways. Once a nightly ships
# an executable ld.lld under ${ROCM_PATH}/llvm/bin on its own, the script
# changes nothing and prints a RETIRE banner instead -- the signal to delete
# it along with its COPY/RUN in docker/Dockerfile.ci. Independently, if FlyDSL
# (or its bundled MLIR) stops looking at $ROCM_PATH/llvm/bin/ld.lld and uses
# lib/llvm/bin, HIP_CLANG_PATH, TRITON_HIP_LLD_PATH, or PATH instead, this
# shim can be deleted even while the stub directory remains; that case will
# not print the banner. PATH can stay: it is not this shim.

set -euo pipefail

readonly rocm_root="${ROCM_PATH:-${ROCM_HOME:-/opt/rocm}}"
readonly llvm_bin="${rocm_root}/lib/llvm/bin"
readonly classic_bin="${rocm_root}/llvm/bin"

if [[ -x "${classic_bin}/ld.lld" ]]; then
    echo "==============================================================" >&2
    echo " setup_rocm_llvm_layout: RETIRE THIS SHIM" >&2
    echo " ${classic_bin}/ld.lld is already executable:" >&2
    echo "     $(readlink -f "${classic_bin}/ld.lld")" >&2
    echo " No extra tool symlinks were created. Delete" >&2
    echo " docker/setup_rocm_llvm_layout.sh and its COPY/RUN in" >&2
    echo " docker/Dockerfile.ci. Keep PATH=/opt/rocm/lib/llvm/bin in" >&2
    echo " the Dockerfile ENV unless a nightly also puts that on PATH." >&2
    echo "==============================================================" >&2
    exit 0
fi

if [[ ! -x "${llvm_bin}/ld.lld" ]]; then
    echo "ld.lld is missing from both ${classic_bin} and ${llvm_bin};" >&2
    echo "is the amdrocm-core LLVM toolchain installed in this layer?" >&2
    exit 1
fi

mkdir -p "${classic_bin}"

linked=0
for src in "${llvm_bin}"/*; do
    dest="${classic_bin}/$(basename "${src}")"
    if [[ -e "${src}" && ! -e "${dest}" ]]; then
        ln -s "${src}" "${dest}"
        linked=$((linked + 1))
    fi
done

if [[ ! -x "${classic_bin}/ld.lld" ]]; then
    echo "linked ${linked} names from ${llvm_bin} into ${classic_bin}" >&2
    echo "but ${classic_bin}/ld.lld is still not executable" >&2
    exit 1
fi

echo "linked ${linked} LLVM toolchain names ${llvm_bin} -> ${classic_bin}"
echo "ld.lld -> $(readlink -f "${classic_bin}/ld.lld")"
