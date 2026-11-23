#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

# Generate a matrix from GPU runner labels, resolving gfx identifiers first
# and falling back to MI product names.
# Expects env var: GPU_RUNNERS (comma-separated list)

GPU_RUNNERS=$(echo "${GPU_RUNNERS:-}" | xargs)

if [ -z "$GPU_RUNNERS" ]; then
  echo "Error: gpu_runners is empty"
  exit 1
fi

TIMEOUT=1440  # 24h default timeout

MATRIX_JSON='{"include":['
FIRST=true

IFS=',' read -ra TAGS <<< "$GPU_RUNNERS"
for TAG in "${TAGS[@]}"; do
  TAG=$(echo "$TAG" | xargs)
  [ -z "$TAG" ] && continue

  # Runner labels may carry a prefix (e.g. itt-gfx942); benchmark tag filtering
  # only cares about the gfx identifier, if the label contains one.
  GFX_ARCH=$(echo "${TAG,,}" | grep -oE 'gfx[0-9][0-9a-z]*' | head -1 || true)

  # Whole SKU tokens are separated by "-", "_" or "."; compare resolved archs
  # rather than product counts so repeated SKUs do not become ambiguous.
  if [ -z "$GFX_ARCH" ]; then
    UNRESOLVED=false
    IFS='-_.' read -ra TOKENS <<< "${TAG,,}"
    for TOKEN in "${TOKENS[@]}"; do
      if [[ "$TOKEN" =~ ^mi[0-9]+[axp]?$ ]]; then
        case "$TOKEN" in
          mi300|mi300a|mi300x|mi308|mi308x|mi325|mi325x) PRODUCT_ARCH="gfx942" ;;
          mi350|mi350x|mi350p|mi355|mi355x) PRODUCT_ARCH="gfx950" ;;
          *) UNRESOLVED=true; continue ;;
        esac
        if [ -n "$GFX_ARCH" ] && [ "$GFX_ARCH" != "$PRODUCT_ARCH" ]; then
          UNRESOLVED=true
        fi
        GFX_ARCH="$PRODUCT_ARCH"
      fi
    done
    if [ "$UNRESOLVED" = "true" ]; then
      GFX_ARCH=""
    fi
  fi

  if [ -z "$GFX_ARCH" ]; then
    echo "::warning::Runner label '${TAG}' has no resolved gfx_arch (unknown or conflicting architecture); architecture filtering is disabled."
  fi

  if [ "$FIRST" = "false" ]; then MATRIX_JSON="${MATRIX_JSON},"; fi
  MATRIX_JSON="${MATRIX_JSON}{\"arch\":\"${TAG}\",\"runner\":\"${TAG}\",\"gfx_arch\":\"${GFX_ARCH}\",\"timeout\":${TIMEOUT}}"
  FIRST=false
done

MATRIX_JSON="${MATRIX_JSON}]}"
echo "matrix=${MATRIX_JSON}" >> "$GITHUB_OUTPUT"
echo "Generated matrix: ${MATRIX_JSON}"
