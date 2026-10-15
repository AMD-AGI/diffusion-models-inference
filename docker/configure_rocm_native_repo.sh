#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

readonly release_id="${ROCM_RELEASE_ID:?ROCM_RELEASE_ID must be set}"
readonly repo_url="https://nightly.repo.amd.com/rocm/core/packages/ubuntu2404/${release_id}"

echo "deb [trusted=yes] ${repo_url} stable main" \
    > /etc/apt/sources.list.d/rocm-nightly.list
apt-get update -qq >&2

deb_series="$(
    apt-cache show amdrocm-runtime-dev |
        sed -nE 's/^Depends: amdrocm-runtime-dev([0-9]+\.[0-9]+).*$/\1/p' |
        sort -u
)"
if [[ ! "${deb_series}" =~ ^[0-9]+\.[0-9]+$ ]]; then
    echo "Could not resolve one ROCm package series from ${repo_url}" >&2
    exit 1
fi
readonly deb_series

echo "ROCm repository: native (${repo_url})" >&2
echo "ROCm package series: ${deb_series} (resolved)" >&2
printf '%s\n' "${deb_series}"
