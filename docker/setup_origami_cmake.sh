#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
#
# Provide a CMake package for origami so find_package(hipblaslt) can configure.
#
# hipBLASLt's installed hipblaslt-package-config.cmake does:
#   find_dependency(origami CONFIG
#       PATHS "${CMAKE_CURRENT_LIST_DIR}/../origami"
#             "${CMAKE_CURRENT_LIST_DIR}/origami"
#       NO_DEFAULT_PATH)
# CMAKE_PREFIX_PATH is ignored. Nightly debs currently ship liborigami.so in
# amdrocm-blas-host but omit lib/cmake/origami from amdrocm-blas-dev, so
# PyTorch's LoadHIP.cmake dies at find_package(hipblaslt REQUIRED).
#
# The fix is a one-file CMake config that imports the .so already on disk as
# roc::origami. It is not a copy of the upstream origami-config.cmake.in
# (that includes origami-targets.cmake and hip).
#
# This shim is expected to expire. Once a nightly ships origami-config.cmake
# (or origamiConfig.cmake) next to hipBLASLt on its own, the script changes
# nothing and prints a RETIRE banner instead -- the signal to delete it along
# with its COPY/RUN in docker/Dockerfile.ci. Independently, if hipBLASLt
# stops find_dependency(origami), this shim can be deleted even while the
# .so remains unpackaged as a CMake package; that case will not print the
# banner.

set -euo pipefail

readonly rocm_root="${ROCM_HOME:-${ROCM_PATH:-/opt/rocm}}"
readonly shim_marker="setup_origami_cmake: SHIM"
readonly dest_dir="${rocm_root}/lib/cmake/origami"
readonly dest="${dest_dir}/origami-config.cmake"

# Paths hipBLASLt searches (NO_DEFAULT_PATH). First existing config wins.
config_candidates=(
    "${rocm_root}/lib/cmake/origami/origami-config.cmake"
    "${rocm_root}/lib/cmake/origami/origamiConfig.cmake"
    "${rocm_root}/lib/cmake/hipblaslt/origami/origami-config.cmake"
    "${rocm_root}/lib/cmake/hipblaslt/origami/origamiConfig.cmake"
)

existing=""
for candidate in "${config_candidates[@]}"; do
    if [[ -f "${candidate}" ]]; then
        existing="${candidate}"
        break
    fi
done

if [[ -n "${existing}" ]] && ! grep -q "${shim_marker}" "${existing}"; then
    echo "==============================================================" >&2
    echo " setup_origami_cmake: RETIRE THIS SHIM" >&2
    echo " find_package(origami) already has a CMake config at" >&2
    echo "     ${existing}" >&2
    echo " No stub was written. Delete docker/setup_origami_cmake.sh" >&2
    echo " and its COPY/RUN in docker/Dockerfile.ci." >&2
    echo "==============================================================" >&2
    exit 0
fi

lib=""
for candidate in \
    "${rocm_root}/lib/liborigami.so" \
    "${rocm_root}/lib/liborigami.so.1"; do
    if [[ -e "${candidate}" ]]; then
        lib="${candidate}"
        break
    fi
done

if [[ -z "${lib}" ]]; then
    echo "origami CMake config is missing and liborigami.so was not found" >&2
    echo "under ${rocm_root}/lib; is the amdrocm-blas-host deb installed" >&2
    echo "in this layer?" >&2
    exit 1
fi

mkdir -p "${dest_dir}"
cat > "${dest}" <<EOF
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
# ${shim_marker}

get_filename_component(_ORIGAMI_PREFIX "\${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)
set(_ORIGAMI_SO "\${_ORIGAMI_PREFIX}/lib/liborigami.so")
if(NOT EXISTS "\${_ORIGAMI_SO}")
    set(_ORIGAMI_SO "\${_ORIGAMI_PREFIX}/lib/liborigami.so.1")
endif()
if(NOT EXISTS "\${_ORIGAMI_SO}")
    message(FATAL_ERROR
        "setup_origami_cmake shim: liborigami.so not found under \${_ORIGAMI_PREFIX}/lib")
endif()

if(NOT TARGET roc::origami)
    add_library(roc::origami SHARED IMPORTED)
    set_target_properties(roc::origami PROPERTIES
        IMPORTED_LOCATION "\${_ORIGAMI_SO}"
    )
    if(EXISTS "\${_ORIGAMI_PREFIX}/include")
        set_property(TARGET roc::origami APPEND PROPERTY
            INTERFACE_INCLUDE_DIRECTORIES "\${_ORIGAMI_PREFIX}/include")
    endif()
endif()

set(origami_FOUND TRUE)
unset(_ORIGAMI_SO)
unset(_ORIGAMI_PREFIX)
EOF

echo "wrote ${dest} importing ${lib}"
echo "roc::origami -> $(readlink -f "${lib}")"
