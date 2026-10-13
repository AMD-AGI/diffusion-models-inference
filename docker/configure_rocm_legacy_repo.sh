#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

readonly release_id="${ROCM_RELEASE_ID:?ROCM_RELEASE_ID must be set}"
readonly deb_series="${ROCM_DEB_SERIES:?ROCM_DEB_SERIES must be set for the legacy repository}"
readonly repo_url="https://rocm.nightlies.amd.com/packages-multi-arch/deb/${release_id}"

if [[ ! "${deb_series}" =~ ^[0-9]+\.[0-9]+$ ]]; then
    echo "Invalid ROCM_DEB_SERIES: ${deb_series}" >&2
    exit 1
fi

echo "deb [trusted=yes] ${repo_url} stable main" \
    > /etc/apt/sources.list.d/rocm-nightly.list
apt-get update -qq >&2

echo "ROCm repository: legacy (${repo_url})" >&2
echo "ROCm package series: ${deb_series} (configured)" >&2
printf '%s\n' "${deb_series}"
