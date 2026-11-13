#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

# Resolve registry layer-cache refs for a docker buildx build.
# Expects env vars: CACHE_IMAGE_NAME, CHANNEL, REF, CACHE_SCOPE,
# EXTRA_CACHE_FROM, EXPORT, EXPORT_TAG, DISABLE_CACHE
# Outputs: scope, cache_from_flags, cache_to_flag, export_ref, live_ref to
# GITHUB_OUTPUT

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# The helper path is resolved relative to this action at runtime.
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../../../scripts/cache-scope.sh"

# Empty output means the ref does not exist. The raw manifest is hashed rather
# than read through --format, because a cache-only manifest is not an image and
# imagetools cannot parse it as one.
manifest_digest() {
  local raw
  if ! raw=$(docker buildx imagetools inspect --raw "$1" 2>/dev/null); then
    return 0
  fi
  printf '%s' "$raw" | sha256sum | cut -d' ' -f1
}

SCOPE=$(slug "${CACHE_SCOPE:-$REF}")
if [ -z "$SCOPE" ]; then
  echo "::error::Could not derive a cache scope from ref '${REF}'"
  exit 1
fi

OWN_REF="${CACHE_IMAGE_NAME}:${CHANNEL}-${SCOPE}"
MAIN_REF="${CACHE_IMAGE_NAME}:${CHANNEL}-main"

echo "Cache scope: ${SCOPE}"

CACHE_FROM_FLAGS=""
if [ "${DISABLE_CACHE}" = "true" ]; then
  echo "Cache disabled, importing nothing"
else
  EXTRA_REFS=()
  read -r -a EXTRA_REFS <<< "${EXTRA_CACHE_FROM}" || true

  SEEN=""
  for CANDIDATE in "${OWN_REF}" "${MAIN_REF}" "${EXTRA_REFS[@]}"; do
    [ -n "${CANDIDATE}" ] || continue
    case " ${SEEN} " in *" ${CANDIDATE} "*) continue ;; esac
    SEEN="${SEEN} ${CANDIDATE}"

    # A missing ref must not fail the build: the builder imports cache straight
    # from the registry, and the first build of a scope has nothing to read.
    if [ -n "$(manifest_digest "${CANDIDATE}")" ]; then
      echo "  hit  ${CANDIDATE}"
      CACHE_FROM_FLAGS="${CACHE_FROM_FLAGS} --cache-from type=registry,ref=${CANDIDATE}"
    else
      echo "  miss ${CANDIDATE}"
    fi
  done
fi

CACHE_TO_FLAG=""
EXPORT_REF=""
LIVE_REF=""
if [ "${EXPORT}" = "true" ]; then
  # Exports never write the scoped ref directly: a mode=max cache holds builder
  # stages that never reach an image, so it has to pass the secret scan before
  # the scan job promotes the candidate to the ref builds import from.
  if [ -z "${EXPORT_TAG:-}" ]; then
    echo "::error::export_tag is required when exporting cache"
    exit 1
  fi
  EXPORT_REF="${CACHE_IMAGE_NAME}:${EXPORT_TAG}"
  LIVE_REF="${OWN_REF}"
  # mode=max is load-bearing: the expensive work lives in builder stages that
  # never reach the final image, and mode=min cannot describe them.
  # ignore-error keeps a failed cache push from failing the image push; the
  # caller checks afterwards whether the candidate exists.
  CACHE_TO_FLAG="--cache-to type=registry,ref=${EXPORT_REF},mode=max,image-manifest=true,oci-mediatypes=true,ignore-error=true"
  echo "Exporting cache to candidate ${EXPORT_REF} (live ref ${LIVE_REF})"
fi

{
  echo "scope=${SCOPE}"
  echo "cache_from_flags=${CACHE_FROM_FLAGS# }"
  echo "cache_to_flag=${CACHE_TO_FLAG}"
  echo "export_ref=${EXPORT_REF}"
  echo "live_ref=${LIVE_REF}"
} >> "$GITHUB_OUTPUT"
