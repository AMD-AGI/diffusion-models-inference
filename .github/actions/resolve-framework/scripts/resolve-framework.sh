#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

# Single source of truth for per-framework image builds. Cache channel values
# are part of registry cache tag names; changing one orphans existing caches.

set -euo pipefail

: "${FRAMEWORK:?}" "${TARGET:?}"
VARS_JSON="${VARS_JSON:-}"
[ -n "$VARS_JSON" ] || VARS_JSON='{}'

var_or() {
  local value
  value="$(jq -r --arg name "$1" '.[$name] // empty' <<<"$VARS_JSON")"
  echo "${value:-$2}"
}

staging_image=""
build_target=""
case "${FRAMEWORK}:${TARGET}" in
  xdit:rocm)
    label="xDiT"
    image="$(var_or CORE_IMAGE amdsiloai/pytorch-xdit-core)"
    staging_image="$(var_or STAGING_IMAGE amdsiloai/pytorch-xdit-staging)"
    dockerfile="docker/Dockerfile.ci"
    build_target="core"
    cache_channel="core"
    ;;
  sgld:rocm)
    label="SGL-D"
    image="$(var_or SGLD_IMAGE amdsiloai/pytorch-xdit-sglang)"
    staging_image="$image"
    dockerfile="docker/Dockerfile.sgld"
    cache_channel="sgld"
    ;;
  vllmo:rocm)
    label="vLLM-Omni"
    image="$(var_or VLLM_OMNI_IMAGE amdsiloai/pytorch-xdit-vllm)"
    staging_image="$image"
    dockerfile="docker/Dockerfile.vllm_omni"
    cache_channel="vllm-omni"
    ;;
  xdit:cuda)
    label="xDiT"
    image="$(var_or NV_IMAGE amdsiloai/pytorch-xdit-nvidia)"
    dockerfile="docker/Dockerfile.cuda"
    cache_channel="nv"
    ;;
  sgld:cuda)
    label="SGL-D"
    image="$(var_or SGLD_NV_IMAGE amdsiloai/pytorch-xdit-sglang-nvidia)"
    dockerfile="docker/Dockerfile.sgld.cuda"
    cache_channel="sgld-nv"
    ;;
  vllmo:cuda)
    label="vLLM-Omni"
    image="$(var_or VLLM_OMNI_NV_IMAGE amdsiloai/pytorch-xdit-vllm-nvidia)"
    dockerfile="docker/Dockerfile.vllm_omni.cuda"
    cache_channel="vllm-omni-nv"
    ;;
  *)
    case "$TARGET" in
      rocm|cuda)
        echo "::error::Unsupported framework '${FRAMEWORK}'. Choose xdit, sgld, or vllmo."
        ;;
      *)
        echo "::error::Unsupported target '${TARGET}'. Choose rocm or cuda."
        ;;
    esac
    exit 1
    ;;
esac

{
  echo "image=${image}"
  echo "staging_image=${staging_image}"
  echo "dockerfile=${dockerfile}"
  echo "build_target=${build_target}"
  echo "cache_channel=${cache_channel}"
  echo "label=${label}"
} >> "$GITHUB_OUTPUT"
